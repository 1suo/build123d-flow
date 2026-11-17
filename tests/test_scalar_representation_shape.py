import unittest

import flow_runtime


class ScalarRepresentationShapeTests(unittest.TestCase):
    def test_vector_and_numeric_tuple_union_remains_one_nominal_value(self):
        class Vector:
            pass

        Vector.__module__ = "build123d.geometry"
        annotation = Vector | tuple[float, float, float]
        metadata = flow_runtime._annotation_metadata(annotation)

        self.assertEqual(metadata["data_shape"], {
            "kind": "union",
            "options": [
                {
                    "kind": "item",
                    "type": "Vector",
                    "type_id": "build123d.geometry.Vector",
                    "type_provider": "build123d",
                    "type_mro": ["build123d.geometry.Vector"],
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
        })
        self.assertNotIn("data_structure", metadata)
        self.assertEqual(metadata.get("data_access"), "structure")


if __name__ == "__main__":
    unittest.main()
