"""Static checks for the non-executing Python graph importer."""

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

    def test_importer_uses_python_ast(self):
        calls = {
            node.func.id for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(any(
            isinstance(node, ast.Attribute) and node.attr == "parse"
            for node in ast.walk(self.importer)
        ))
        self.assertTrue(any(
            isinstance(node, ast.Attribute) and node.attr == "Call"
            for node in ast.walk(self.importer)
        ))

    def test_importer_never_executes_or_evaluates_source(self):
        forbidden = {"exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(forbidden.intersection(calls))

    def test_importer_reports_unsupported_statements(self):
        source = ast.unparse(self.importer)
        self.assertIn("Unsupported statement", source)
        self.assertIn("lineno", source)

    def test_importer_builds_serializable_graph_records(self):
        source = ast.unparse(self.importer)
        for required in ("graph_nodes", "graph_edges", "'position'", "'definition'", "'params'"):
            self.assertIn(required, source)

    def test_with_statements_become_first_class_execution_scopes(self):
        source = ast.unparse(self.importer)
        self.assertIn("ast.With", source)
        self.assertIn("execution_scopes", source)
        self.assertIn("execution_scope_path", source)
        self.assertIn("scope_binding", source)
        self.assertIn("scope_result", source)
        self.assertIn("resolve_context_manager", source)
        self.assertNotIn("Multiple context managers in one with-statement", source)

    def test_importer_uses_one_receiver_contract_resolver(self):
        source = ast.unparse(self.importer)
        self.assertIn("def output_contract", source)
        self.assertIn("def receiver_score", source)
        self.assertIn("def resolve_member", source)
        self.assertIn("call_node=node", source)
        self.assertIn("property_only=True", source)

    def test_runtime_enters_exits_and_unwinds_scopes(self):
        runtime = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
        evaluator = next(
            node for node in runtime.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_graph_json"
        )
        source = ast.unparse(evaluator)
        self.assertIn("__enter__", source)
        self.assertIn("__exit__", source)
        self.assertIn("failed_scopes", source)
        self.assertIn("restore_scope_value", source)

    def test_scope_operations_are_not_cached(self):
        runtime = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
        evaluator = next(
            node for node in runtime.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_graph_json"
        )
        source = ast.unparse(evaluator)
        self.assertIn("not scope_path", source)


if __name__ == "__main__":
    unittest.main()
