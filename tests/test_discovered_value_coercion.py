import unittest
from types import SimpleNamespace
from unittest.mock import patch

import flow_runtime


class DiscoveredValueCoercionTests(unittest.TestCase):
    class Translation:
        def __init__(self, x, y, z):
            self.coordinates = (x, y, z)

    class Orientation:
        def __init__(self, angles):
            self.angles = tuple(angles)

    class ArbitraryFrame:
        def __init__(self, origin, direction):
            self.origin = tuple(origin)
            self.direction = tuple(direction)

    def test_receiver_construction_uses_discovered_identity_not_type_allowlist(self):
        with patch.object(
            flow_runtime, "_resolve_discovered_type",
            return_value=self.Translation,
        ):
            result = flow_runtime._coerce_graph_receiver(
                [1, 2, 3], {"type": "vendor.geometry.Translation"}
            )
        self.assertEqual(result.coordinates, (1, 2, 3))

    def test_exported_alias_resolves_through_discovered_symbol_registry(self):
        alias = {
            "Vector3": {
                "kind": "type",
                "type_id": "vendor.geometry.Translation",
            }
        }
        registry = {
            "vendor.geometry.Translation": {
                "id": "vendor.geometry.Translation",
                "module": "fake.geometry",
                "name": "Translation",
            }
        }
        with patch.object(flow_runtime, "_SYMBOL_REGISTRY", alias), patch.object(
            flow_runtime, "_TYPE_REGISTRY", registry
        ), patch.object(
            flow_runtime.importlib, "import_module",
            return_value=SimpleNamespace(Translation=self.Translation),
        ):
            resolved = flow_runtime._resolve_discovered_type("Vector3")
        self.assertIs(resolved, self.Translation)

    def test_single_sequence_constructor_is_detected_from_signature(self):
        result = flow_runtime._construct_discovered_type(
            self.Orientation, [10, 20, 30]
        )
        self.assertEqual(result.angles, (10, 20, 30))

    def test_arbitrary_discovered_object_marker_is_restored(self):
        with patch.object(
            flow_runtime, "_resolve_discovered_type",
            return_value=self.ArbitraryFrame,
        ):
            result = flow_runtime._restore_discovered_value({
                "__flow_object__": "ArbitraryFrame",
                "args": [[1, 2, 3], [0, 0, 1]],
                "kwargs": {},
            })
        self.assertEqual(result.origin, (1, 2, 3))
        self.assertEqual(result.direction, (0, 0, 1))

    def test_any_semantics_still_restore_discovered_runtime_values(self):
        with patch.object(
            flow_runtime, "_resolve_discovered_type",
            return_value=self.Orientation,
        ):
            result = flow_runtime._coerce_annotation(
                {"__flow_object__": "Orientation", "value": [0, 90, 0]},
                None,
            )
        self.assertEqual(result.angles, (0, 90, 0))

    def test_display_type_name_is_discovered_not_rewritten_to_vector3(self):
        class Vector:
            pass
        Vector.__module__ = "build123d.geometry"
        self.assertEqual(flow_runtime._type_name(Vector), "Vector")

    def test_collection_shape_is_never_inferred_from_a_type_label(self):
        self.assertEqual(
            flow_runtime._socket_data_structure({"type": "List"}),
            "item",
        )
        self.assertEqual(
            flow_runtime._socket_data_structure({
                "type": "Any",
                "data_structure": "list",
            }),
            "list",
        )

    def test_vector_operations_are_not_a_hardcoded_flow_node_family(self):
        definitions = {}
        callables = {}
        flow_runtime._register_flow_nodes(definitions, callables)
        self.assertFalse(any(key.startswith("core.vector.") for key in definitions))

    def test_any_does_not_become_a_nominal_typing_type(self):
        metadata = flow_runtime._annotation_metadata(__import__("typing").Any)
        self.assertNotIn("type_id", metadata)
        self.assertNotIn("type_provider", metadata)

    def test_collection_shape_and_semantic_type_are_emitted_separately(self):
        self.Translation.__module__ = "build123d.geometry"
        metadata = flow_runtime._annotation_metadata(list[self.Translation])
        self.assertEqual(metadata["data_structure"], "list")
        self.assertEqual(metadata["type_id"], "build123d.geometry.Translation")
        self.assertEqual(metadata["type_provider"], "build123d")

    def test_unknown_discovered_object_fails_closed(self):
        with patch.object(
            flow_runtime, "_resolve_discovered_type",
            return_value=None,
        ):
            with self.assertRaisesRegex(TypeError, "undiscovered type"):
                flow_runtime._restore_discovered_value({
                    "__flow_object__": "UnknownFrame",
                    "value": [1, 2, 3],
                })


if __name__ == "__main__":
    unittest.main()
