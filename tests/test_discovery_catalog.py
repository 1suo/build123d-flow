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

    def test_iterable_geometry_values_remain_item_receivers(self):
        for type_name in ("Plane", "Curve"):
            native_type = getattr(runtime.b3d, type_name, None)
            if native_type is None:
                continue
            socket = runtime._receiver_socket(native_type)
            self.assertEqual(
                socket.get("data_access"), "item",
                "{} is one geometry value, not a collection socket".format(type_name),
            )
            self.assertNotIn("data_structure", socket)

    def test_declared_shapelist_receiver_is_structure_access(self):
        shape_list = getattr(runtime.b3d, "ShapeList", None)
        if shape_list is None:
            self.skipTest("This build123d version has no public ShapeList")
        socket = runtime._receiver_socket(shape_list)
        self.assertEqual(socket.get("data_access"), "structure")
        self.assertEqual(socket.get("data_structure"), "list")

    def test_context_manager_result_properties_are_discovered(self):
        discovered = []
        for record in runtime._TYPE_REGISTRY.values():
            attribute = record.get("context_result_attribute")
            if not attribute:
                continue
            property_id = next((
                owner_id + ".property." + attribute
                for owner_id in record.get("mro", ())
                if owner_id + ".property." + attribute in runtime._CATALOG
            ), None)
            self.assertIsNotNone(property_id)
            output = runtime._CATALOG[property_id]["outputs"][0]
            self.assertTrue(output.get("is_geometry"))
            self.assertEqual(output["type"], record.get("context_result_type"))
            discovered.append(record["id"])
        self.assertTrue(discovered, "No context-manager result property was discovered")

    def test_box_geometry_capability_is_discovered_on_its_constructor_output(self):
        box_type = runtime.b3d.Box
        identity = box_type.__module__ + "." + box_type.__name__
        definition = next(
            runtime._CATALOG[node_id]
            for node_id in runtime._TYPE_REGISTRY[identity]["constructor_node_ids"]
            if node_id in runtime._CATALOG
        )
        output = definition["outputs"][0]
        self.assertEqual(output["type"], "Box")
        self.assertTrue(output.get("is_geometry"))
        self.assertEqual(output.get("semantic_type_id"), identity)

    def test_catalog_records_the_installed_distribution_version(self):
        self.assertEqual(self.bundle["build123d_version"], "0.13.1.dev342+g03b257b1c")
        self.assertEqual(self.bundle["ocp_distribution_version"], "8.0.1.1.0")
        self.assertGreater(len(self.bundle["types"]), 0)
        self.assertGreater(len(self.bundle["nodes"]), 0)


if __name__ == "__main__":
    unittest.main()
