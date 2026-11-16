import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

from type_catalog import CatalogError, TypeCatalog, build_type_registry


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
            "schema_version": 1,
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
            "schema_version": 1,
            "types": list(registry.values()),
            "nodes": [],
        })
        with self.assertRaises(CatalogError):
            catalog.resolve_type("Duplicate")

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
                "schema_version": 1,
                "types": list(registry.values()),
                "nodes": [],
            })

    def test_mro_must_be_ordered_and_unique(self):
        with self.assertRaises(CatalogError):
            TypeCatalog({
                "schema_version": 1,
                "types": [{
                    "id": "build123d.Invalid",
                    "name": "Invalid",
                    "mro": ["build123d.Base", "build123d.Invalid"],
                }],
                "nodes": [],
            })


if __name__ == "__main__":
    unittest.main()
