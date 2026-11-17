import unittest

from type_catalog import build_type_registry


class DiscoveredTypeNameTests(unittest.TestCase):
    def test_vector_socket_type_is_its_discovered_class_name(self):
        vector = type("Vector", (), {"__module__": "build123d.geometry"})
        record = build_type_registry([vector], {})["build123d.geometry.Vector"]
        self.assertEqual(record["socket_type"], "Vector")
        self.assertNotEqual(record["socket_type"], "Vector3")


if __name__ == "__main__":
    unittest.main()
