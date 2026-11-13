"""Python importer frontend: parsing and import/name-environment analysis.

This module deliberately has no build123d dependency. It owns Python syntax
validation and the source-level import environment; graph semantics and
lowering consume its result in the runtime layer.
"""
from dataclasses import dataclass
import ast


VIEWER_API = frozenset({"show", "show_object", "show_all", "reset_show", "set_defaults"})


@dataclass(frozen=True)
class ImportEnvironment:
    aliases: dict
    imported: dict
    viewer_modules: dict
    viewer_functions: frozenset

    def is_viewer_call(self, node):
        if not isinstance(node, ast.Call):
            return False
        function = node.func
        if isinstance(function, ast.Name):
            return function.id in self.viewer_functions
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            return (
                self.viewer_modules.get(function.value.id) == "ocp_vscode"
                and function.attr in VIEWER_API
            )
        return False


@dataclass(frozen=True)
class ParsedPython:
    source: str
    tree: ast.Module
    imports: ImportEnvironment


def parse_source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Python source is empty")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("Python syntax error at line {}:{}: {}".format(
            exc.lineno or 1, exc.offset or 1, exc.msg
        )) from exc
    return tree


def resolve_imports(tree):
    """Resolve supported imports without importing or executing user modules."""
    aliases = {"build123d": "b3d"}
    imported = {}
    viewer_modules = {}
    viewer_functions = set()

    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                if item.name == "build123d":
                    aliases[item.asname or item.name] = "b3d"
                elif item.name == "ocp_vscode":
                    viewer_modules[item.asname or item.name] = "ocp_vscode"
                else:
                    raise ValueError("Unsupported import at line {}: {}".format(
                        statement.lineno, item.name))
        elif isinstance(statement, ast.ImportFrom):
            if statement.module == "build123d" or (
                statement.module and statement.module.startswith("build123d.")
            ):
                for item in statement.names:
                    if item.name != "*":
                        imported[item.asname or item.name] = item.name
            elif statement.module == "ocp_vscode":
                for item in statement.names:
                    if item.name == "*":
                        viewer_functions.update(VIEWER_API)
                    elif item.name in VIEWER_API:
                        viewer_functions.add(item.asname or item.name)
                    else:
                        raise ValueError("Unsupported ocp_vscode import at line {}: {}".format(
                            statement.lineno, item.name))
            else:
                raise ValueError(
                    "Only build123d and ocp_vscode imports are supported "
                    "(line {})".format(statement.lineno)
                )

    return ImportEnvironment(
        aliases=aliases,
        imported=imported,
        viewer_modules=viewer_modules,
        viewer_functions=frozenset(viewer_functions),
    )


def analyze_source(source):
    """Run the syntax and import-resolution frontend stages."""
    tree = parse_source(source)
    return ParsedPython(source, tree, resolve_imports(tree))
