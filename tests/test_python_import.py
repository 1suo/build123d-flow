"""Structural checks for the conservative Python-to-graph translator."""

import ast
import unittest
from pathlib import Path


RUNTIME_PATH = Path(__file__).parents[1] / "public" / "python" / "flow_runtime.py"


class PythonImportSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
        cls.importer = next(
            node for node in cls.module.body
            if isinstance(node, ast.FunctionDef) and node.name == "import_python_code"
        )

    def test_importer_uses_builtin_ast_directly(self):
        calls = [
            node for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertTrue(any(
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ast"
            and node.func.attr == "parse"
            for node in calls
        ))
        self.assertFalse(any(
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("python_importer_")
            for node in ast.walk(self.importer)
        ))

    def test_importer_keeps_catalog_resolution_local(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("resolve_member", functions)
        source = ast.unparse(functions["resolve_member"])
        self.assertIn("variants", source)
        self.assertIn("receiver_type", source)

    def test_importer_never_executes_or_evaluates_source(self):
        forbidden = {"exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(forbidden.intersection(calls))

    def test_member_resolution_matches_catalog_method_ids_not_display_names(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        importer = functions["import_python_code"]
        resolver = next(
            node for node in ast.walk(importer)
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_member"
        )
        source = ast.unparse(resolver)
        self.assertIn("candidate_id.rsplit", source)
        self.assertIn("member_name", source)
        self.assertNotIn("candidate.get('name')", source)
        self.assertNotIn('candidate.get("name")', source)

    def test_catalog_discovers_typed_build123d_collection_members(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        type_name = ast.unparse(functions["_type_name"])
        discovery = ast.unparse(functions["discover"])
        self.assertIn("getattr(origin", type_name)
        self.assertIn("__module__", type_name)
        self.assertIn('return annotation.__name__', type_name)
        member_loop = next(
            node for node in ast.walk(functions["discover"])
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "cls"
            and isinstance(node.iter, ast.Name)
            and node.iter.id == "api_classes"
        )
        member_loop_source = ast.unparse(member_loop)
        self.assertNotIn("_candidate_rejection", member_loop_source)
        self.assertIn("for owner in getattr(cls, '__mro__'", member_loop_source)

    def test_importer_reports_unsupported_statements_with_location(self):
        source = ast.unparse(self.importer)
        self.assertIn("Unsupported statement", source)
        self.assertIn("lineno", source)

    def test_importer_builds_native_graph_records(self):
        source = ast.unparse(self.importer)
        for required in ("graph_nodes", "graph_edges", "'position'", "'definition'", "'params'"):
            self.assertIn(required, source)

    def test_context_managers_are_preserved_as_execution_scopes(self):
        source = ast.unparse(self.importer)
        for required in ("ast.With", "execution_scopes", "execution_scope_path",
                         "scope_binding", "scope_result", "resolve_context_manager"):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
