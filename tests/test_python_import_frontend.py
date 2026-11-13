"""Behavioral tests for the Python importer frontend stage."""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "public" / "python"))
from python_importer_frontend import analyze_source, parse_source, resolve_imports


class PythonImporterFrontendTests(unittest.TestCase):
    def test_parse_stage_returns_python_module(self):
        parsed = parse_source("from build123d import Box\nBox(1, 2, 3)")
        self.assertIsInstance(parsed, ast.Module)

    def test_import_resolution_preserves_aliases_without_importing_modules(self):
        frontend = analyze_source(
            "import build123d as b\n"
            "from build123d import Box as Block\n"
            "import ocp_vscode as viewer\n"
            "from ocp_vscode import show as display\n"
        )
        self.assertEqual(frontend.imports.aliases["b"], "b3d")
        self.assertEqual(frontend.imports.imported["Block"], "Box")
        self.assertEqual(frontend.imports.viewer_modules["viewer"], "ocp_vscode")
        self.assertIn("display", frontend.imports.viewer_functions)

    def test_viewer_calls_are_recognized_by_resolved_alias(self):
        frontend = analyze_source(
            "from ocp_vscode import show as display\n"
            "import ocp_vscode as viewer\n"
            "display(obj)\nviewer.show(obj)"
        )
        calls = [n for n in ast.walk(frontend.tree) if isinstance(n, ast.Call)]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(frontend.imports.is_viewer_call(call) for call in calls))

    def test_unsupported_import_has_source_location(self):
        with self.assertRaisesRegex(ValueError, r"Unsupported import at line 1"):
            analyze_source("import os")

    def test_unsupported_from_import_has_source_location(self):
        with self.assertRaisesRegex(ValueError, r"Only build123d and ocp_vscode imports.*line 1"):
            analyze_source("from pathlib import Path")

    def test_syntax_error_is_reported_as_source_diagnostic(self):
        with self.assertRaisesRegex(ValueError, r"Python syntax error at line 1:"):
            analyze_source("Box(")


if __name__ == "__main__":
    unittest.main()
