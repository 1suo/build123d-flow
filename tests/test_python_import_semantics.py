"""Behavioral tests for kernel-neutral catalog member resolution."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "public" / "python"))
from python_importer_semantics import CatalogResolver


def definition(name, receiver_type, variants=None):
    return {
        "name": name,
        "inputs": [{
            "name": "self", "type": receiver_type,
            "variants": variants or [receiver_type],
        }],
    }


class CatalogResolverTests(unittest.TestCase):
    def setUp(self):
        self.definitions = {
            "kernel.shape.method.vertices": definition("vertices", "Shape", ["Shape"]),
            "kernel.solid.method.vertices": definition("vertices", "Solid", ["Solid"]),
            "kernel.shape.property.volume": definition("volume", "Shape", ["Shape"]),
            "kernel.mesh.field.volume": definition("volume", "Mesh", ["Mesh"]),
            "kernel.any.method.describe": definition("describe", "Any", ["Any"]),
        }
        self.resolver = CatalogResolver(self.definitions)

    def test_exact_receiver_beats_broad_shape_contract(self):
        result = self.resolver.resolve_member("vertices", "Solid")
        self.assertEqual(result.definition_id, "kernel.solid.method.vertices")

    def test_property_resolution_is_indexed_separately(self):
        result = self.resolver.resolve_member("volume", "Mesh", property_only=True)
        self.assertEqual(result.definition_id, "kernel.mesh.field.volume")

    def test_universal_any_method_is_fallback(self):
        result = self.resolver.resolve_member("describe", "Sketch")
        self.assertEqual(result.definition_id, "kernel.any.method.describe")

    def test_unmatched_receiver_is_not_guessed(self):
        result = self.resolver.resolve_member("vertices", "Sketch")
        self.assertIsNone(result.definition_id)
        self.assertFalse(result.candidates)

    def test_equal_best_candidates_are_reported_as_ambiguous(self):
        self.definitions["other.solid.method.vertices"] = definition(
            "vertices", "Solid", ["Solid"]
        )
        resolver = CatalogResolver(self.definitions)
        result = resolver.resolve_member("vertices", "Solid")
        self.assertIsNone(result.definition_id)
        self.assertEqual(len(result.candidates), 2)
        self.assertIsNotNone(result.reason)


if __name__ == "__main__":
    unittest.main()
