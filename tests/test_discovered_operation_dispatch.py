import inspect
import unittest
from unittest.mock import patch

import flow_runtime


class DiscoveredOperationDispatchTests(unittest.TestCase):
    def _run(self, definition, function, inputs, config=None):
        operation_id = definition["id"]
        with patch.dict(flow_runtime._CATALOG, {operation_id: definition}, clear=True), patch.dict(
            flow_runtime._CATALOG_CALLABLES, {operation_id: function}, clear=True
        ), patch.dict(
            flow_runtime._CATALOG_SIGNATURES,
            {operation_id: inspect.signature(function)},
            clear=True,
        ), patch.dict(
            flow_runtime._CATALOG_HINT_TARGETS, {operation_id: False}, clear=True
        ):
            return flow_runtime.execute_discovered_operation(
                operation_id, inputs, config
            )

    def test_item_inputs_map_lists_with_longest_matching(self):
        def add(a, b):
            return a + b
        definition = {
            "id": "vendor.math.add",
            "inputs": [
                {"name": "a", "type": "Number", "data_access": "item"},
                {"name": "b", "type": "Number", "data_access": "item"},
            ],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        self.assertEqual(
            self._run(definition, add, {"a": [1, 2, 3], "b": [10, 20]}),
            {"result": [11, 22, 23]},
        )

    def test_shortest_and_cross_matching_are_applied_by_declared_policy(self):
        def add(a, b):
            return a + b
        definition = {
            "id": "vendor.math.add",
            "inputs": [
                {"name": "a", "type": "Number", "data_access": "item"},
                {"name": "b", "type": "Number", "data_access": "item"},
            ],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        self.assertEqual(
            self._run(
                definition, add, {"a": [1, 2, 3], "b": [10, 20]},
                {"dataMatching": "shortest"},
            ),
            {"result": [11, 22]},
        )
        self.assertEqual(
            self._run(
                definition, add, {"a": [1, 2], "b": [10, 20, 30]},
                {"dataMatching": "cross"},
            ),
            {"result": [[11, 21, 31], [12, 22, 32]]},
        )

    def test_shared_python_loop_source_keeps_inputs_aligned_even_under_cross_mode(self):
        def add(a, b):
            return a + b
        definition = {
            "id": "vendor.math.add",
            "inputs": [
                {"name": "a", "type": "Number", "data_access": "item"},
                {"name": "b", "type": "Number", "data_access": "item"},
            ],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        config = {
            "dataMatching": "cross",
            "pythonLoopSources": [{
                "node_id": "loop-source", "output_name": "result", "matching": "shortest"
            }],
            "inputSources": {
                "a": [{"nodeId": "loop-source", "outputId": "result"}],
                "b": [{"nodeId": "loop-source", "outputId": "result"}],
            },
        }
        self.assertEqual(
            self._run(definition, add, {"a": [1, 2, 3], "b": [10, 20]}, config),
            {"result": [11, 22]},
        )

    def test_independent_python_loop_sources_can_cross(self):
        def add(a, b):
            return a + b
        definition = {
            "id": "vendor.math.add",
            "inputs": [
                {"name": "a", "type": "Number", "data_access": "item"},
                {"name": "b", "type": "Number", "data_access": "item"},
            ],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        config = {
            "dataMatching": "cross",
            "pythonLoopSources": [
                {"node_id": "loop-a", "output_name": "result", "matching": "longest"},
                {"node_id": "loop-b", "output_name": "result", "matching": "longest"},
            ],
            "inputSources": {
                "a": [{"nodeId": "loop-a", "outputId": "result"}],
                "b": [{"nodeId": "loop-b", "outputId": "result"}],
            },
        }
        self.assertEqual(
            self._run(definition, add, {"a": [1, 2], "b": [10, 20, 30]}, config),
            {"result": [11, 21, 31, 12, 22, 32]},
        )

    def test_structure_access_preserves_collection_as_one_value(self):
        def count(items):
            return len(items)
        definition = {
            "id": "vendor.collection.count",
            "inputs": [{
                "name": "items", "type": "Any",
                "data_structure": "list", "data_access": "structure",
            }],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        self.assertEqual(
            self._run(definition, count, {"items": [1, 2, 3]}),
            {"result": 3},
        )

    def test_nested_stream_is_mapped_recursively_as_a_tree(self):
        def double(value):
            return value * 2
        definition = {
            "id": "vendor.math.double",
            "inputs": [{"name": "value", "type": "Number", "data_access": "item"}],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        self.assertEqual(
            self._run(definition, double, {"value": [[1, 2], [3]]}),
            {"result": [[2, 4], [6]]},
        )

    def test_instance_methods_bind_receiver_using_discovered_socket(self):
        class Frame:
            def translate(self, amount):
                return amount + 5
        frame = Frame()
        function = Frame.translate
        definition = {
            "id": "vendor.geometry.Frame.translate",
            "inputs": [
                {"name": "self", "type": "vendor.geometry.Frame", "data_access": "item"},
                {"name": "amount", "type": "Number", "data_access": "item"},
            ],
            "outputs": [{"name": "result", "type": "Number"}],
        }
        with patch.object(flow_runtime, "_coerce_graph_receiver", return_value=frame):
            self.assertEqual(
                self._run(definition, function, {"self": frame, "amount": 7}),
                {"result": 12},
            )

    def test_context_manager_scopes_fail_closed_in_stateless_dispatch(self):
        definition = {
            "id": "vendor.geometry.operation",
            "inputs": [],
            "outputs": [{"name": "result", "type": "Any"}],
        }
        with self.assertRaisesRegex(RuntimeError, "context-manager scopes"):
            self._run(definition, lambda: 1, {}, {"executionScopePath": ["scope-1"]})

    def test_unknown_operation_fails_closed(self):
        with self.assertRaisesRegex(KeyError, "Unknown discovered operation"):
            flow_runtime.execute_discovered_operation("missing.operation", {})


if __name__ == "__main__":
    unittest.main()
