import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

import flow_runtime as runtime


class RecursiveDataContractTests(unittest.TestCase):
    def test_nested_list_annotations_preserve_tree_shape_and_item_type(self):
        import typing

        shape = runtime._annotation_data_shape(list[list[int]])
        self.assertEqual(shape, {
            "kind": "tree",
            "item": {
                "kind": "list",
                "item": {"kind": "item", "type": "Number"},
            },
        })

    def test_recursive_data_shape_is_authoritative_for_execution_structure(self):
        socket = {
            "type": "Any",
            "data_structure": "item",
            "data_shape": {
                "kind": "tree",
                "item": {
                    "kind": "list",
                    "item": {"kind": "item", "type": "Box"},
                },
            },
        }
        self.assertEqual(runtime._socket_data_structure(socket), "tree")

    def test_tuple_and_mapping_shapes_are_recursive(self):
        import typing

        tuple_shape = runtime._annotation_data_shape(tuple[int, list[str]])
        self.assertEqual(tuple_shape, {
            "kind": "tuple",
            "items": [
                {"kind": "item", "type": "Number"},
                {"kind": "list", "item": {"kind": "item", "type": "String"}},
            ],
        })
        map_shape = runtime._annotation_data_shape(dict[str, list[typing.Any]])
        self.assertEqual(map_shape, {
            "kind": "map",
            "key": {"kind": "item", "type": "String"},
            "value": {"kind": "list", "item": {"kind": "item", "type": "Any"}},
        })

    def test_runtime_shape_discovery_does_not_require_static_socket_type(self):
        class FakeShape:
            pass

        first, second, mapping_key = FakeShape(), FakeShape(), FakeShape()
        fake_kernel = SimpleNamespace(Shape=FakeShape)
        with patch.object(runtime, "b3d", fake_kernel):
            discovered = list(runtime._iter_shapes({
                mapping_key: [first],
                "branch-b": ({second},),
            }))
        self.assertEqual(
            {id(value) for value in discovered},
            {id(first), id(second), id(mapping_key)},
        )

    def test_recursive_shape_discovery_ignores_cycles(self):
        class FakeShape:
            pass

        shape = FakeShape()
        cyclic = [shape]
        cyclic.append(cyclic)
        with patch.object(runtime, "b3d", SimpleNamespace(Shape=FakeShape)):
            self.assertEqual(list(runtime._iter_shapes(cyclic)), [shape])

    def test_iterable_domain_object_is_not_misclassified_as_collection_socket(self):
        class IterableDomain:
            def __iter__(self):
                return iter((1, 2, 3))
            def __len__(self):
                return 3
            def __getitem__(self, index):
                return (1, 2, 3)[index]

        socket = runtime._receiver_socket(IterableDomain)
        self.assertEqual(socket["type"], "IterableDomain")
        self.assertEqual(socket["data_access"], "item")
        self.assertNotIn("data_structure", socket)

    def test_declared_collection_subclass_receives_structure_access(self):
        class ExplicitCollection(list):
            pass

        socket = runtime._receiver_socket(ExplicitCollection)
        self.assertEqual(socket["data_structure"], "list")
        self.assertEqual(socket["data_access"], "structure")

    def test_native_operator_dispatch_uses_runtime_subclass_override(self):
        import inspect

        class Base:
            def __xor__(self, position):
                return "base"

        class Derived(Base):
            def __xor__(self, position):
                return "derived"

        signature = inspect.signature(Base.__xor__)
        result = runtime._invoke_discovered_operator_callable(
            lambda mapped: Base.__xor__(mapped["self"], mapped["position"]),
            "__xor__",
            {"self": Derived(), "position": 0},
            signature,
        )
        self.assertEqual(result, "derived")

    def test_reflected_operator_dispatch_uses_python_protocol(self):
        import inspect

        class Left:
            def __mul__(self, other):
                return NotImplemented

        class Right:
            def __rmul__(self, other):
                return "reflected"

        signature = inspect.signature(Right.__rmul__)
        result = runtime._invoke_discovered_operator_callable(
            lambda mapped: Right.__rmul__(mapped["self"], mapped["other"]),
            "__rmul__",
            {"self": Right(), "other": Left()},
            signature,
        )
        self.assertEqual(result, "reflected")

    def test_discovered_enum_identity_is_not_collapsed_to_generic_enum(self):
        import enum

        class NativeEnum(enum.Enum):
            FIRST = 1

        NativeEnum.__module__ = "build123d.enums"
        self.assertEqual(runtime._type_name(NativeEnum), "NativeEnum")

    def test_discovered_value_serialization_uses_protocols_not_type_names(self):
        from type_catalog import _serialize_discovered_value

        class Vector:
            def __iter__(self):
                return iter((1.0, 2.0, 3.0))

        class Rotation:
            def to_tuple(self):
                return (10.0, 20.0, 30.0)

        Vector.__module__ = "build123d.geometry"
        Rotation.__module__ = "build123d.geometry"
        self.assertEqual(_serialize_discovered_value(Vector()), {
            "__flow_object__": "Vector",
            "value": [1.0, 2.0, 3.0],
        })
        self.assertEqual(_serialize_discovered_value(Rotation()), {
            "__flow_object__": "Rotation",
            "value": [10.0, 20.0, 30.0],
        })

    def test_discovered_class_identity_is_not_reduced_to_vector3(self):
        class Vector:
            pass

        Vector.__module__ = "build123d.geometry"
        self.assertEqual(runtime._type_name(Vector), "Vector")


if __name__ == "__main__":
    unittest.main()
