import enum
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

from type_catalog import CatalogError, TypeCatalog, build_symbol_registry, build_type_registry


def _fake_type(name, bases=(object,)):
    return type(name, bases, {"__module__": "build123d"})


class GeneratedTypeRegistryTests(unittest.TestCase):
    def test_registry_keeps_discovered_types_without_node_candidates(self):
        base = _fake_type("CatalogBase")
        child = _fake_type("CatalogChild", (base,))
        registry = build_type_registry([base, child], {})
        self.assertEqual(set(registry), {
            "build123d.CatalogBase", "build123d.CatalogChild",
        })
        self.assertEqual(
            registry["build123d.CatalogChild"]["mro"][:2],
            ["build123d.CatalogChild", "build123d.CatalogBase"],
        )
        self.assertEqual(registry["build123d.CatalogBase"]["constructor_node_ids"], [])

    def test_node_eligibility_does_not_remove_type(self):
        cls = _fake_type("Unconstructible")
        nodes = {
            "build123d.Unconstructible.some_method": {
                "id": "build123d.Unconstructible.some_method",
            }
        }
        registry = build_type_registry([cls], nodes)
        record = registry["build123d.Unconstructible"]
        self.assertIn("build123d.Unconstructible", registry)
        self.assertEqual(
            record["member_node_ids"],
            ["build123d.Unconstructible.some_method"],
        )

    def test_catalog_resolves_inheritance_and_native_operator_mro(self):
        base = _fake_type("OperatorBase")
        child = _fake_type("OperatorChild", (base,))
        nodes = {
            "build123d.OperatorBase.operator.__add__": {
                "id": "build123d.OperatorBase.operator.__add__",
            }
        }
        registry = build_type_registry([base, child], nodes)
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": list(nodes.values()),
        })
        self.assertTrue(catalog.is_subtype("OperatorChild", "OperatorBase"))
        self.assertEqual(
            catalog.resolve_operator("OperatorChild", "__add__"),
            "build123d.OperatorBase.operator.__add__",
        )

    def test_ambiguous_short_names_are_rejected(self):
        first = type("Duplicate", (), {"__module__": "build123d.alpha"})
        second = type("Duplicate", (), {"__module__": "build123d.beta"})
        registry = build_type_registry([first, second], {})
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": [],
        })
        with self.assertRaises(CatalogError):
            catalog.resolve_type("Duplicate")

    def test_generic_type_variables_survive_unresolved_forward_references(self):
        import typing
        from flow_runtime import _typevar_names, _typevar_occurs_in_collection

        annotation = typing.Union[
            typing.ForwardRef("_ShapeT"),
            typing.ForwardRef("list[_ShapeT]"),
        ]
        self.assertEqual(_typevar_names(annotation), {"_ShapeT"})
        self.assertTrue(_typevar_occurs_in_collection(annotation, "_ShapeT"))

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(CatalogError):
            TypeCatalog({"schema_version": 999, "types": [], "nodes": []})

    def test_dangling_type_node_reference_is_rejected(self):
        cls = _fake_type("Dangling")
        registry = build_type_registry([cls], {
            "build123d.Dangling.missing": {"id": "build123d.Dangling.missing"},
        })
        with self.assertRaises(CatalogError):
            TypeCatalog({
                "schema_version": 4,
                "types": list(registry.values()),
                "nodes": [],
            })

    def test_mro_must_be_ordered_and_unique(self):
        with self.assertRaises(CatalogError):
            TypeCatalog({
                "schema_version": 4,
                "types": [{
                    "id": "build123d.Invalid",
                    "name": "Invalid",
                    "mro": ["build123d.Base", "build123d.Invalid"],
                }],
                "nodes": [],
            })

    def test_enum_members_are_discovered_and_serializable(self):
        class Unit(enum.Enum):
            MM = "mm"
            IN = "in"
        Unit.__module__ = "build123d.units"
        registry = build_type_registry([Unit], {})
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": [],
        })
        self.assertEqual(
            catalog.resolve_enum_member("Unit", "MM"),
            {"__flow_enum__": ["build123d.units", "Unit", "MM"]},
        )

    def test_context_manager_capability_is_discovered(self):
        class Manager:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        Manager.__module__ = "build123d.builders"
        record = build_type_registry([Manager], {})["build123d.builders.Manager"]
        self.assertTrue(record["supports_context_manager"])

    def test_class_level_value_objects_are_discovered(self):
        class VectorValue:
            __module__ = "build123d.geometry"
            def __iter__(self):
                return iter((1.0, 2.0, 3.0))
        class Plane:
            XY = VectorValue()
        Plane.__module__ = "build123d.geometry"
        registry = build_type_registry([Plane], {})
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": [],
        })
        self.assertEqual(
            catalog.resolve_class_value("Plane", "XY"),
            {"__flow_object__": "VectorValue", "value": [1.0, 2.0, 3.0]},
        )

    def test_metaclass_class_properties_are_discovered(self):
        class VectorValue:
            __module__ = "build123d.geometry"
            def __iter__(self):
                return iter((0.0, 0.0, 0.0))
        class PlaneMeta(type):
            @property
            def XY(cls):
                return VectorValue()
        class Plane(metaclass=PlaneMeta):
            pass
        Plane.__module__ = "build123d.geometry"
        registry = build_type_registry([Plane], {})
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": [],
        })
        self.assertEqual(
            catalog.resolve_class_value("Plane", "XY"),
            {"__flow_object__": "VectorValue", "value": [0.0, 0.0, 0.0]},
        )

    def test_root_symbols_discover_aliases_and_unit_constants(self):
        import types
        class Rotation:
            pass
        Rotation.__module__ = "build123d.geometry"
        module = types.ModuleType("build123d")
        module.Rot = Rotation
        module.MM = 1.0
        module.CM = 10.0
        symbols = build_symbol_registry(module)
        registry = build_type_registry([Rotation], {})
        catalog = TypeCatalog({
            "schema_version": 4,
            "types": list(registry.values()),
            "nodes": [],
            "symbols": symbols,
        })
        self.assertEqual(
            catalog.resolve_symbol("Rot"),
            {"kind": "type", "type_id": "build123d.geometry.Rotation"},
        )
        self.assertEqual(catalog.resolve_symbol("MM"), {"kind": "value", "value": 1.0})
        self.assertEqual(catalog.resolve_symbol("CM"), {"kind": "value", "value": 10.0})


if __name__ == "__main__":
    unittest.main()
