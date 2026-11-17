import typing
import unittest

import flow_runtime


class RecursiveDataShapeMetadataTests(unittest.TestCase):
    def test_nested_lists_preserve_tree_shape_and_leaf_semantic_type(self):
        self.assertEqual(
            flow_runtime._annotation_data_shape(list[list[int]]),
            {
                "kind": "tree",
                "item": {
                    "kind": "list",
                    "item": {"kind": "item", "type": "Number"},
                },
            },
        )

    def test_fixed_tuple_keeps_each_nested_element_shape(self):
        self.assertEqual(
            flow_runtime._annotation_data_shape(tuple[int, list[str]]),
            {
                "kind": "tuple",
                "items": [
                    {"kind": "item", "type": "Number"},
                    {
                        "kind": "list",
                        "item": {"kind": "item", "type": "String"},
                    },
                ],
            },
        )

    def test_maps_keep_key_and_value_shapes_independently(self):
        self.assertEqual(
            flow_runtime._annotation_data_shape(dict[str, list[typing.Any]]),
            {
                "kind": "map",
                "key": {"kind": "item", "type": "String"},
                "value": {
                    "kind": "list",
                    "item": {"kind": "item", "type": "Any"},
                },
            },
        )

    def test_outer_execution_structure_comes_from_recursive_shape(self):
        socket = {
            "type": "Any",
            "data_shape": {
                "kind": "tree",
                "item": {
                    "kind": "list",
                    "item": {"kind": "item", "type": "build123d.Solid"},
                },
            },
        }
        self.assertEqual(flow_runtime._socket_data_structure(socket), "tree")

    def test_union_of_item_and_collection_is_dynamic_not_flattened(self):
        socket = {
            "type": "Any",
            "data_shape": {
                "kind": "union",
                "options": [
                    {"kind": "item", "type": "build123d.Vector"},
                    {
                        "kind": "list",
                        "item": {"kind": "item", "type": "Number"},
                    },
                ],
            },
        }
        self.assertEqual(flow_runtime._socket_data_structure(socket), "dynamic")


if __name__ == "__main__":
    unittest.main()
