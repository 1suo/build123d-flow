import inspect
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

import flow_runtime as runtime


class LiveDiscoveryCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog_path = os.environ.get("BUILD123D_CATALOG_PATH")
        cls.bundle = (
            json.loads(Path(catalog_path).read_text(encoding="utf-8"))
            if catalog_path else json.loads(runtime.catalog_bundle_json())
        )
        cls.catalog = runtime._type_catalog()

    def test_discovery_contains_every_public_package_class(self):
        report = {}
        modules = runtime._discover_modules(report)
        self.assertIsNone(report.get("module_walk_error"))
        self.assertEqual(report.get("module_import_failures"), [])
        expected = set()
        for module in modules:
            for obj in vars(module).values():
                if not inspect.isclass(obj):
                    continue
                if getattr(obj, "__module__", "").split(".")[0] != "build123d":
                    continue
                name = getattr(obj, "__name__", "")
                if not name or name.startswith("_"):
                    continue
                expected.add(obj.__module__ + "." + name)
        self.assertEqual(expected, set(self.catalog.types))

    def test_type_mros_match_native_python_mros(self):
        classes = {}
        for module in runtime._discover_modules({}):
            for obj in vars(module).values():
                if inspect.isclass(obj) and getattr(obj, "__module__", "").split(".")[0] == "build123d":
                    classes[obj.__module__ + "." + obj.__name__] = obj
        for identity, record in self.catalog.types.items():
            cls = classes[identity]
            expected = [
                owner.__module__ + "." + owner.__name__
                for owner in cls.__mro__
                if inspect.isclass(owner)
            ]
            self.assertEqual(record["mro"], expected, identity)

    def test_class_level_methods_are_discovered_as_nodes(self):
        class_level = [
            node_id for node_id in self.catalog.nodes
            if ".static." in node_id or ".classmethod." in node_id
        ]
        self.assertTrue(class_level, "No public static/class methods were discovered")

    def test_type_node_references_are_complete(self):
        node_ids = set(self.catalog.nodes)
        for identity, record in self.catalog.types.items():
            references = (
                record.get("constructor_node_ids", [])
                + record.get("member_node_ids", [])
            )
            self.assertTrue(
                set(references).issubset(node_ids),
                "{} has dangling node references".format(identity),
            )

    def test_box_operator_resolution_uses_discovered_mro(self):
        box = runtime.b3d.Box
        identity = box.__module__ + "." + box.__name__
        node_ids = set(self.catalog.nodes)
        expected = next(
            (
                owner.__module__ + "." + owner.__name__ + ".operator.__add__"
                for owner in box.__mro__
                if owner.__module__ + "." + owner.__name__ + ".operator.__add__" in node_ids
            ),
            None,
        )
        self.assertIsNotNone(expected)
        self.assertEqual(self.catalog.resolve_operator(identity, "__add__"), expected)

    def test_catalog_records_the_installed_distribution_version(self):
        self.assertEqual(self.bundle["build123d_version"], "0.13.0")
        self.assertEqual(self.bundle["ocp_distribution_version"], "8.0.1.1.0")
        self.assertGreater(len(self.bundle["types"]), 0)
        self.assertGreater(len(self.bundle["nodes"]), 0)


if __name__ == "__main__":
    unittest.main()
