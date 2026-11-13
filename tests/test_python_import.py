"""Static Python-to-graph import and roundtrip tests."""

import unittest

import flow_runtime


class PythonImportTests(unittest.TestCase):
    def test_import_generated_style_build123d_calls(self):
        graph = flow_runtime.import_python_code(
            "import build123d as b3d\n"
            "from build123d import Box as _api_callable_0\n"
            "_node_1 = _api_callable_0(length=20, width=30, height=10)\n"
            "result = _node_1\n"
        )
        self.assertEqual(len(graph["nodes"]), 1)
        node = graph["nodes"][0]
        self.assertEqual(node["data"]["definition"]["name"], "Box")
        self.assertEqual(node["data"]["params"]["length"], 20)
        self.assertEqual(node["data"]["params"]["width"], 30)
        self.assertEqual(node["data"]["params"]["height"], 10)
        self.assertEqual(graph["edges"], [])

    def test_import_export_import_roundtrip(self):
        source = (
            "from build123d import Box\n"
            "block = Box(length=20, width=30, height=10)\n"
            "result = block\n"
        )
        first_graph = flow_runtime.import_python_code(source)
        generated = flow_runtime.export_python_code(first_graph)
        second_graph = flow_runtime.import_python_code(generated)
        self.assertEqual(len(first_graph["nodes"]), len(second_graph["nodes"]))
        self.assertEqual(
            first_graph["nodes"][0]["data"]["params"],
            second_graph["nodes"][0]["data"]["params"],
        )

    def test_import_rejects_control_flow_without_execution(self):
        with self.assertRaisesRegex(ValueError, "Unsupported statement.*For"):
            flow_runtime.import_python_code(
                "from build123d import Box\n"
                "for index in range(3):\n"
                "    Box(10, 10, 10)\n"
            )

    def test_import_reports_syntax_error(self):
        with self.assertRaisesRegex(ValueError, "Python syntax error"):
            flow_runtime.import_python_code("from build123d import\n")

    def test_import_rejects_non_build123d_imports(self):
        with self.assertRaisesRegex(ValueError, "Only build123d imports"):
            flow_runtime.import_python_code("import os\n")


if __name__ == "__main__":
    unittest.main()
