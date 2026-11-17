import unittest

from perigram_graph import semantic_descriptor


class DiscoveredProviderIdentityTests(unittest.TestCase):
    def test_type_name_prefix_never_assigns_provider_ownership(self):
        self.assertEqual(
            semantic_descriptor("build123d.geometry.Vector"),
            {
                "kind": "nominal",
                "provider": "perigram.python",
                "typeId": "build123d.geometry.Vector",
            },
        )

    def test_discovery_provider_metadata_assigns_native_provider(self):
        self.assertEqual(
            semantic_descriptor("build123d.geometry.Vector", "build123d"),
            {
                "kind": "nominal",
                "provider": "build123d",
                "typeId": "build123d.geometry.Vector",
            },
        )


if __name__ == "__main__":
    unittest.main()
