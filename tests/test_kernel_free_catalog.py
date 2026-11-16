import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

from type_catalog import TypeCatalog


class KernelFreeCatalogLoadTests(unittest.TestCase):
    def setUp(self):
        catalog_path = os.environ.get("BUILD123D_CATALOG_PATH")
        if not catalog_path:
            self.skipTest("BUILD123D_CATALOG_PATH is not set")
        self.catalog = TypeCatalog.from_json(Path(catalog_path).read_text(encoding="utf-8"))

    def test_loading_catalog_does_not_import_kernel(self):
        self.assertNotIn("build123d", sys.modules)
        self.assertNotIn("OCP", sys.modules)

    def test_discovered_box_operator_resolves_from_serialized_mro(self):
        box = self.catalog.resolve_type("Box")
        operator_id = self.catalog.resolve_operator(box["id"], "__add__")
        self.assertIn(operator_id, self.catalog.nodes)

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
