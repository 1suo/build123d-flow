import json
import sys
import types
import unittest
from unittest.mock import patch

import perigram_runtime


class NativeValue:
    pass


class PerigramRuntimeValueTests(unittest.TestCase):
    def setUp(self):
        perigram_runtime.clear_runtime()

    def tearDown(self):
        perigram_runtime.clear_runtime()

    def test_builtin_data_shapes_round_trip_without_flattening_tuples(self):
        value = {
            "items": [1, 2.5, "three"],
            "frame": (4, 5, 6),
            "labels": {"left", "right"},
            "mapping": {1: "one", 2: "two"},
        }
        encoded = perigram_runtime._encode(value, "graph-a")
        decoded = perigram_runtime._decode(encoded, "graph-a")
        self.assertEqual(decoded, value)
        self.assertIsInstance(decoded["frame"], tuple)
        self.assertIsInstance(decoded["labels"], set)
        self.assertEqual(decoded["mapping"], value["mapping"])

    def test_native_objects_cross_boundary_only_as_session_scoped_handles(self):
        value = NativeValue()
        encoded = perigram_runtime._encode(value, "graph-a")
        self.assertEqual(encoded["__perigram_runtime__"], "value-ref")
        self.assertEqual(encoded["session"], "graph-a")
        self.assertEqual(encoded["typeId"], __name__ + ".NativeValue")
        self.assertIs(perigram_runtime._decode(encoded, "graph-a"), value)
        with self.assertRaisesRegex(ValueError, "different graph session"):
            perigram_runtime._decode(encoded, "graph-b")

    def test_runtime_dispatch_decodes_inputs_and_retains_native_outputs(self):
        native = NativeValue()
        input_ref = perigram_runtime._encode(native, "graph-dispatch")
        perigram_runtime._REF_COUNTS[input_ref["id"]] = 1
        config = {"graphId": "graph-dispatch", "nodeId": "node-2"}
        with patch("flow_runtime.execute_discovered_operation", return_value={"result": native}) as execute:
            encoded_json = perigram_runtime.execute_operation(
                "build123d.consume",
                {"shape": input_ref},
                config,
            )
        execute.assert_called_once()
        self.assertIs(execute.call_args.args[1]["shape"], native)
        result = json.loads(encoded_json)
        self.assertIs(perigram_runtime._decode(result["result"], "graph-dispatch"), native)


class PersistentScopeFrameTests(unittest.TestCase):
    def setUp(self):
        perigram_runtime.clear_runtime()
        self.module = types.ModuleType("perigram_test_scopes")
        self.enter_frame = None
        self.events = []
        test_case = self

        class Outer:
            def __init__(self):
                self.result = "outer-result"

            def __enter__(self):
                test_case.enter_frame = sys._getframe(1)
                test_case.events.append("enter-outer")
                return self

            def __exit__(self, *exc):
                test_case.events.append("exit-outer")
                return False

        class Inner:
            def __init__(self):
                self.result = "inner-result"

            def __enter__(self):
                caller = sys._getframe(1)
                if caller is not test_case.enter_frame:
                    raise AssertionError("Nested builder scopes must enter from one persistent frame")
                test_case.events.append("enter-inner")
                return self

            def __exit__(self, *exc):
                test_case.events.append("exit-inner")
                return False

        self.module.Outer = Outer
        self.module.Inner = Inner
        sys.modules[self.module.__name__] = self.module

    def tearDown(self):
        perigram_runtime.clear_runtime()
        sys.modules.pop(self.module.__name__, None)

    def _config(self, node_id, path, binding=None, result=None):
        scopes = [
            {
                "id": "outer",
                "module": self.module.__name__,
                "name": "Outer",
                "args": [],
                "kwargs": {},
                "has_binding": True,
                "result_attribute": "result",
            },
            {
                "id": "inner",
                "module": self.module.__name__,
                "name": "Inner",
                "args": [],
                "kwargs": {},
                "has_binding": True,
                "result_attribute": "result",
            },
        ]
        return {
            "graphId": "scope-graph",
            "nodeId": node_id,
            "executionScopePath": path,
            "executionScopes": scopes,
            **({"scopeBinding": binding} if binding else {}),
            **({"scopeResult": result} if result else {}),
        }

    def test_nested_builder_scopes_share_one_persistent_python_frame(self):
        outer = json.loads(perigram_runtime.execute_operation(
            "core.python.scope.binding", {}, self._config("scope-outer", ["outer"], binding="outer")
        ))
        self.assertEqual(outer["__perigram_runtime__"], "value-ref")

        inner = json.loads(perigram_runtime.execute_operation(
            "core.python.scope.binding", {}, self._config("scope-inner", ["outer", "inner"], binding="inner")
        ))
        self.assertEqual(inner["__perigram_runtime__"], "value-ref")

        with patch("flow_runtime.execute_discovered_operation", return_value={"result": "body"}):
            body = json.loads(perigram_runtime.execute_operation(
                "build123d.BodyOperation", {}, self._config("scope-body", ["outer", "inner"])
            ))
        self.assertEqual(body["result"], "body")

        inner_result = json.loads(perigram_runtime.execute_operation(
            "core.python.scope.result", {}, self._config("scope-inner-result", ["outer"], result="inner")
        ))
        self.assertEqual(inner_result["result"], "inner-result")

        outer_result = json.loads(perigram_runtime.execute_operation(
            "core.python.scope.result", {}, self._config("scope-outer-result", [], result="outer")
        ))
        self.assertEqual(outer_result["result"], "outer-result")
        self.assertEqual(self.events, [
            "enter-outer", "enter-inner", "exit-inner", "exit-outer"
        ])


if __name__ == "__main__":
    unittest.main()
