import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

from type_catalog import TypeCatalog
import flow_runtime as runtime


class KernelFreeCatalogLoadTests(unittest.TestCase):
    def setUp(self):
        catalog_path = os.environ.get("BUILD123D_CATALOG_PATH")
        if not catalog_path:
            self.skipTest("BUILD123D_CATALOG_PATH is not set")
        payload = Path(catalog_path).read_text(encoding="utf-8")
        self.bundle = json.loads(payload)
        self.catalog = TypeCatalog(self.bundle)
        self.load_result = runtime.load_catalog_bundle(payload)

    def test_loading_catalog_does_not_import_kernel(self):
        self.assertNotIn("build123d", sys.modules)
        self.assertNotIn("OCP", sys.modules)

    def test_production_runtime_loads_catalog_without_kernel(self):
        self.assertIsNone(runtime.b3d)
        self.assertEqual(self.load_result["type_count"], len(self.catalog.types))
        self.assertEqual(self.load_result["node_count"], len(self.catalog.nodes))
        self.assertIsNone(runtime.b3d)
        self.assertNotIn("build123d", sys.modules)

    def test_discovered_box_operator_resolves_from_serialized_mro(self):
        box = self.catalog.resolve_type("Box")
        operator_id = self.catalog.resolve_operator(box["id"], "__add__")
        self.assertIn(operator_id, self.catalog.nodes)

    def test_production_interpreter_translates_box_plus_cylinder_without_kernel(self):
        source = """
from build123d import Box, Cylinder
box = Box(10, 10, 10)
cylinder = Cylinder(2, 10)
result = box + cylinder
"""
        graph = json.loads(runtime.import_python_code(source))
        definitions = [
            node.get("data", {}).get("definition", {}).get("id")
            for node in graph.get("nodes", [])
        ]
        self.assertTrue(
            any(identity and identity.endswith(".Box") for identity in definitions),
            definitions,
        )
        self.assertTrue(
            any(identity and identity.endswith(".Cylinder") for identity in definitions),
            definitions,
        )
        self.assertTrue(
            any(identity and identity.endswith(".operator.__add__") for identity in definitions),
            definitions,
        )
        self.assertIsNone(runtime.b3d)
        self.assertNotIn("build123d", sys.modules)

    def test_all_discovered_type_node_references_resolve(self):
        for identity, record in self.catalog.types.items():
            references = (
                record.get("constructor_node_ids", [])
                + record.get("member_node_ids", [])
            )
            self.assertTrue(
                set(references).issubset(self.catalog.nodes),
                "{} contains dangling node references".format(identity),
            )


if __name__ == "__main__":
    unittest.main()
