import unittest

from perigram_graph import normalize_node_definition, transport_shape


class PerigramRecursiveShapeAdapterTests(unittest.TestCase):
    def test_nested_list_shape_keeps_leaf_nominal_type(self):
        socket = {
            "name": "solids",
            "type": "Any",
            "data_shape": {
                "kind": "tree",
                "item": {
                    "kind": "list",
                    "item": {
                        "kind": "item",
                        "type": "Solid",
                        "type_id": "build123d.topology.three_d.Solid",
                        "type_provider": "build123d",
                    },
                },
            },
        }
        definition = normalize_node_definition({
            "id": "build123d.consume_solids",
            "source": "build123d",
            "inputs": [socket],
            "outputs": [],
        })
        self.assertEqual(transport_shape(socket), "tree")
        self.assertEqual(definition["inputs"][0]["shape"], "tree")
        self.assertEqual(definition["inputs"][0]["semantic"], {
            "kind": "nominal",
            "provider": "build123d",
            "typeId": "build123d.topology.three_d.Solid",
        })

    def test_map_shape_preserves_key_and_nested_value_semantics(self):
        socket = {
            "name": "by_name",
            "type": "Any",
            "data_shape": {
                "kind": "map",
                "key": {"kind": "item", "type": "String"},
                "value": {
                    "kind": "list",
                    "item": {
                        "kind": "item",
                        "type": "Face",
                        "type_id": "build123d.topology.two_d.Face",
                        "type_provider": "build123d",
                    },
                },
            },
        }
        definition = normalize_node_definition({
            "id": "build123d.consume_map",
            "source": "build123d",
            "inputs": [socket],
            "outputs": [],
        })
        self.assertEqual(definition["inputs"][0]["shape"], "map")
        self.assertEqual(definition["inputs"][0]["semantic"], {
            "kind": "map",
            "key": {"kind": "primitive", "name": "string"},
            "value": {
                "kind": "nominal",
                "provider": "build123d",
                "typeId": "build123d.topology.two_d.Face",
            },
        })

    def test_fixed_tuple_shape_preserves_each_position_type(self):
        socket = {
            "name": "frame",
            "type": "Any",
            "data_shape": {
                "kind": "tuple",
                "items": [
                    {"kind": "item", "type": "Number"},
                    {
                        "kind": "item",
                        "type": "Plane",
                        "type_id": "build123d.geometry.Plane",
                        "type_provider": "build123d",
                    },
                ],
            },
        }
        definition = normalize_node_definition({
            "id": "build123d.consume_tuple",
            "source": "build123d",
            "inputs": [socket],
            "outputs": [],
        })
        self.assertEqual(definition["inputs"][0]["shape"], "tuple")
        self.assertEqual(definition["inputs"][0]["semantic"], {
            "kind": "tuple",
            "items": [
                {"kind": "primitive", "name": "number"},
                {
                    "kind": "nominal",
                    "provider": "build123d",
                    "typeId": "build123d.geometry.Plane",
                },
            ],
        })


if __name__ == "__main__":
    unittest.main()
