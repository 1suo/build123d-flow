import ast
import unittest
from pathlib import Path


class UpstreamMutationPolicyTests(unittest.TestCase):
    def test_browser_bootstrap_does_not_mutate_build123d_modules(self):
        setup_path = Path(__file__).resolve().parents[1] / "public" / "python" / "setup.py"
        tree = ast.parse(setup_path.read_text(encoding="utf-8"), filename=str(setup_path))
        module_aliases = {"build123d", "build123d_mesher"}

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets.append(node.target)

            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in module_aliases
                ):
                    self.fail(
                        "Browser bootstrap mutates upstream module attribute "
                        "{} at line {}".format(target.value.id + "." + target.attr, node.lineno)
                    )

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in module_aliases
            ):
                self.fail(
                    "Browser bootstrap uses setattr on upstream module {} at line {}".format(
                        node.args[0].id, node.lineno
                    )
                )


if __name__ == "__main__":
    unittest.main()
