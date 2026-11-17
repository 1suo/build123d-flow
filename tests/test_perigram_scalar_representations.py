import unittest

from perigram_graph import normalize_node_definition, transport_shape


class ScalarRepresentationPerigramTests(unittest.TestCase):
    def test_vector_coordinate_tuple_is_semantic_representation_not_stream(self):
        socket = {
            "name": "vector",
            "type": "Any",
            "type_id": "build123d.geometry.Vector",
            "type_provider": "build123d",
            "data_access": "structure",
            "data_representation": "value",
            "data_shape": {
                "kind": "union",
                "options": [
                    {
                        "kind": "item",
                        "type": "Vector",
                        "type_id": "build123d.geometry.Vector",
                        "type_provider": "build123d",
                    },
                    {
                        "kind": "tuple",
                        "items": [
                            {"kind": "item", "type": "Number"},
                            {"kind": "item", "type": "Number"},
                            {"kind": "item", "type": "Number"},
                        ],
                    },
                ],
            },
        }
        definition = normalize_node_definition({
            "id": "build123d.consume_vector",
            "source": "build123d",
            "inputs": [socket],
            "outputs": [],
        })
        port = definition["inputs"][0]
        self.assertEqual(transport_shape(socket), "item")
        self.assertEqual(port["shape"], "item")
        self.assertEqual(port["semantic"], {
            "kind": "nominal",
            "provider": "build123d",
            "typeId": "build123d.geometry.Vector",
            "representations": [{
                "kind": "tuple",
                "items": [
                    {"kind": "primitive", "name": "number"},
                    {"kind": "primitive", "name": "number"},
                    {"kind": "primitive", "name": "number"},
                ],
            }],
        })


if __name__ == "__main__":
    unittest.main()
