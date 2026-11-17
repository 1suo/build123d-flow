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

        first, second = FakeShape(), FakeShape()
        fake_kernel = SimpleNamespace(Shape=FakeShape)
        with patch.object(runtime, "b3d", fake_kernel):
            discovered = list(runtime._iter_shapes({
                "branch-a": [first],
                "branch-b": ({second},),
            }))
        self.assertEqual({id(value) for value in discovered}, {id(first), id(second)})

    def test_recursive_shape_discovery_ignores_cycles(self):
        class FakeShape:
            pass

        shape = FakeShape()
        cyclic = [shape]
        cyclic.append(cyclic)
        with patch.object(runtime, "b3d", SimpleNamespace(Shape=FakeShape)):
            self.assertEqual(list(runtime._iter_shapes(cyclic)), [shape])

    def test_discovered_class_identity_is_not_reduced_to_vector3(self):
        class Vector:
            pass

        Vector.__module__ = "build123d.geometry"
        self.assertEqual(runtime._type_name(Vector), "Vector")


if __name__ == "__main__":
    unittest.main()
