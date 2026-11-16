import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "public" / "python"))
sys.path.insert(0, str(ROOT / "tests"))

import flow_runtime as runtime
import graph_workflows


class NativeGraphWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.discover(include_full=True)

    def test_production_graph_workflows_on_native_kernel(self):
        results = graph_workflows.run_graph_workflow_tests()
        self.assertGreaterEqual(len(results), 10)
        self.assertTrue(all(result.get("name") for result in results))


if __name__ == "__main__":
    unittest.main()
