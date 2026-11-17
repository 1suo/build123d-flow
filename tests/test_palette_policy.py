import unittest
from types import SimpleNamespace
from unittest.mock import patch

import flow_runtime


class PalettePolicyTests(unittest.TestCase):
    def test_boolean_logic_nodes_live_under_logic_not_a_separate_tab(self):
        self.assertEqual(flow_runtime._FLOW_PALETTE["Boolean"][0], "Logic")
        definitions = {}
        callables = {}
        flow_runtime._register_flow_nodes(definitions, callables)
        self.assertTrue(any(item["palette_category"] == "Logic" for item in definitions.values()))
        self.assertFalse(any(item["palette_category"] == "Boolean" for item in definitions.values()))

    def test_native_shape_boolean_operators_use_discovered_api_palette(self):
        class Shape:
            pass

        Shape.__module__ = "build123d.topology.shape_core"
        with patch.object(flow_runtime, "b3d", SimpleNamespace(Shape=Shape)), patch.dict(
            flow_runtime._NODE_METADATA_OVERRIDES, {}, clear=True
        ):
            flow_runtime._register_kernel_discovery_overrides()
            overrides = list(flow_runtime._NODE_METADATA_OVERRIDES.values())

        self.assertEqual(len(overrides), 3)
        self.assertTrue(all(item["palette_category"] == "API" for item in overrides))
        self.assertTrue(all(item["palette_section"] == "Discovered operators" for item in overrides))
        self.assertFalse(any(item["palette_category"] == "Boolean" for item in overrides))


if __name__ == "__main__":
    unittest.main()
