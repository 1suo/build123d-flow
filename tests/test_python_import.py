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

    def test_importer_delegates_parsing_to_frontend(self):
        calls = {
            node.func.id for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        imports_frontend = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "python_importer_frontend"
            and any(alias.name == "analyze_source" for alias in node.names)
            for node in self.importer.body
        )
        self.assertTrue(imports_frontend)
        self.assertIn("analyze_source", calls)

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


    def test_importer_expression_path_consumes_kernel_neutral_ir(self):
        source = inspect.getsource(self.importer)
        self.assertIn("from python_importer_ir import", source)
        self.assertIn("expression_from_ir", source)

if __name__ == "__main__":
    unittest.main()
