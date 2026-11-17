import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "public" / "python"))

from type_catalog import TypeCatalog
import flow_runtime as runtime


class KernelFreeCatalogLoadTests(unittest.TestCase):
    def setUp(self):
        catalog_path = os.environ.get("BUILD123D_CATALOG_PATH")
        if not catalog_path:
            self.skipTest("BUILD123D_CATALOG_PATH is not set")
        payload = Path(catalog_path).read_text(encoding="utf-8")
        self.bundle = json.loads(payload)
        self.catalog = TypeCatalog(self.bundle)
        self.load_result = runtime.load_catalog_bundle(payload)

    def test_loading_catalog_does_not_import_kernel(self):
        self.assertEqual(self.bundle["schema_version"], TypeCatalog.SCHEMA_VERSION)
        self.assertNotIn("build123d", sys.modules)
        self.assertNotIn("OCP", sys.modules)

    def test_production_runtime_loads_catalog_without_kernel(self):
        self.assertIsNone(runtime.b3d)
        self.assertEqual(self.load_result["type_count"], len(self.catalog.types))
        self.assertEqual(self.load_result["node_count"], len(self.catalog.nodes))
        self.assertIsNone(runtime.b3d)
        self.assertNotIn("build123d", sys.modules)

    def test_extension_method_receiver_preserves_positional_only_order(self):
        for method in ("Generated", "Modified"):
            node_id = "build123d.topology.history.MakeShapeLike." + method
            signature = runtime._CATALOG_SIGNATURES[node_id]
            parameters = list(signature.parameters.values())
            self.assertEqual(parameters[0].name, "self")
            self.assertEqual(parameters[0].kind, parameters[0].POSITIONAL_ONLY)
            self.assertEqual(parameters[1].name, "shape")
            self.assertEqual(parameters[1].kind, parameters[1].POSITIONAL_ONLY)

    def test_discovered_box_operator_resolves_from_serialized_mro(self):
        box = self.catalog.resolve_type("Box")
        operator_id = self.catalog.resolve_operator(box["id"], "__add__")
        self.assertIn(operator_id, self.catalog.nodes)

    def test_production_interpreter_translates_box_plus_cylinder_without_kernel(self):
        source = """
from build123d import Box, Cylinder
box = Box(10, 10, 10)
cylinder = Cylinder(2, 10)
result = box + cylinder
"""
        graph = runtime.import_python_code(source)
        definitions = [
            node.get("data", {}).get("definition", {}).get("id")
            for node in graph.get("nodes", [])
        ]
        self.assertTrue(
            any(identity and identity.rsplit(".overload-", 1)[0].endswith(".Box") for identity in definitions),
            definitions,
        )
        self.assertTrue(
            any(identity and identity.rsplit(".overload-", 1)[0].endswith(".Cylinder") for identity in definitions),
            definitions,
        )
        self.assertTrue(
            any(identity and identity.endswith(".operator.__add__") for identity in definitions),
            definitions,
        )
        self.assertIsNone(runtime.b3d)
        self.assertNotIn("build123d", sys.modules)

    def test_extracted_translator_module_is_kernel_free(self):
        from python_translator import TranslatorServices, import_python_code

        self.assertNotIn("b3d", TranslatorServices._fields)
        source = """
from build123d import Box, Cylinder
box = Box(10, 10, 10)
cylinder = Cylinder(2, 10)
result = box + cylinder
"""
        graph = import_python_code(source, runtime._translator_services(), runtime._type_catalog())
        self.assertTrue(graph.get("nodes"))
        self.assertIsNone(runtime.b3d)
        self.assertNotIn("build123d", sys.modules)
        self.assertNotIn("OCP", sys.modules)

    def test_upstream_graph_validator_rejects_unregistered_embedded_node(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from test_upstream_translator import validate_graph

        graph = {
            "nodes": [{
                "id": "fabricated-node",
                "data": {"definition": {
                    "id": "fabricated.module.call",
                    "inputs": [],
                    "outputs": [],
                }},
            }],
            "edges": [],
        }
        with self.assertRaisesRegex(ValueError, "unregistered definition"):
            validate_graph(graph, "fabricated.py")

    def test_upstream_graph_validator_accepts_generated_scope_binding(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from test_upstream_translator import validate_graph

        graph = {
            "nodes": [{
                "id": "scope-binding",
                "data": {"definition": {
                    "id": "core.python.scope.binding",
                    "inputs": [],
                    "outputs": [{"name": "result", "type": "BuildPart"}],
                }},
            }],
            "edges": [],
        }
        metrics = validate_graph(graph, "scope.py")
        self.assertEqual(metrics["node_count"], 1)

    def test_upstream_graph_validator_accepts_generated_condition_socket(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from test_upstream_translator import validate_graph

        definition_id = next(
            identity for identity in runtime._CATALOG
            if identity.split(".overload-", 1)[0].endswith(".Box")
        )
        definition = runtime._CATALOG[definition_id]
        embedded = dict(definition)
        embedded["inputs"] = [
            dict(socket) for socket in definition.get("inputs", [])
        ] + [{
            "name": "__flow_condition_0",
            "type": "Boolean",
            "required": True,
            "data_access": "item",
        }]
        embedded["outputs"] = [
            dict(socket) for socket in definition.get("outputs", [])
        ]
        graph = {
            "nodes": [{
                "id": "conditional-node",
                "data": {"definition": embedded},
            }],
            "edges": [],
        }
        metrics = validate_graph(graph, "conditional.py")
        self.assertEqual(metrics["node_count"], 1)

    def test_evaluator_preserves_generated_condition_socket_contract(self):
        definition = dict(runtime._CATALOG["core.python.function"])
        definition["inputs"] = [
            dict(socket) for socket in definition["inputs"]
        ] + [{
            "name": "__flow_condition_0",
            "type": "Boolean",
            "required": True,
            "data_access": "item",
        }]
        graph = {
            "nodes": [{
                "id": "conditional-function",
                "type": "api",
                "position": {"x": 0, "y": 0},
                "data": {
                    "definition": definition,
                    "params": {
                        "code": "result = 42",
                        "__flow_condition_0": True,
                    },
                },
            }],
            "edges": [],
        }
        from unittest.mock import patch

        # The callable is inert; this test exercises only graph lowering and
        # evaluator argument filtering, never the CAD kernel.
        with patch.dict(
            runtime._CATALOG_CALLABLES,
            {"core.python.function": lambda code, a=None, b=None, c=None: 42},
        ):
            result = json.loads(runtime.evaluate_graph_json(
                json.dumps(graph), include_previews=False
            ))
        self.assertEqual(result["errors"], {}, result["errors"])
        self.assertIn("conditional-function", result["socket_values"])
        self.assertIsNone(runtime.b3d)

    def test_call_adapter_forwards_extra_keywords_to_variadic_constructor(self):
        import inspect

        def overloaded_constructor(*args, **kwargs):
            return args, kwargs

        signature = inspect.signature(overloaded_constructor)
        result = runtime._call_with_coercion(
            overloaded_constructor,
            {
                "args": (),
                "origin": (0, 0, 142.0),
                "z_dir": (0, 0, 1),
            },
            signature=signature,
            hint_target=False,
        )
        self.assertEqual(result, ((), {
            "origin": (0, 0, 142.0),
            "z_dir": (0, 0, 1),
        }))

    def test_generic_location_operator_infers_shape_result_from_operand(self):
        graph = runtime.import_python_code("""
from build123d import Box, Location
result = Location() * Box(10, 10, 10)
""")
        operator_node = next(
            node for node in graph["nodes"]
            if node.get("data", {}).get("definition", {}).get("id")
            == "build123d.geometry.Location.operator.__mul__"
        )
        output = operator_node["data"]["definition"]["outputs"][0]
        self.assertEqual(output["type"], "Box")
        self.assertNotIn("data_structure", output)
        self.assertIsNone(runtime.b3d)

    def test_generic_location_operator_preserves_discovered_collection_type(self):
        graph = runtime.import_python_code("""
from build123d import Box, Location, ShapeList
result = Location() * ShapeList([Box(10, 10, 10)])
""")
        operator_node = next(
            node for node in graph["nodes"]
            if node.get("data", {}).get("definition", {}).get("id")
            == "build123d.geometry.Location.operator.__mul__"
        )
        output = operator_node["data"]["definition"]["outputs"][0]
        self.assertEqual(output["type"], "ShapeList")
        self.assertIsNone(runtime.b3d)

    def test_discovered_operator_uses_python_reflected_dispatch(self):
        from flow_runtime import _invoke_discovered_operator

        class Left:
            def __mul__(self, other):
                return NotImplemented

        class Right:
            def __rmul__(self, other):
                return "reflected result"

        self.assertEqual(
            _invoke_discovered_operator("__mul__", Left(), Right()),
            "reflected result",
        )

    def test_call_coercion_resolves_nested_forward_refs_before_union_selection(self):
        import collections.abc
        import inspect
        import typing
        from flow_runtime import _call_with_coercion

        def identity(value):
            return value

        annotation = typing.Union[
            typing.ForwardRef("str"),
            collections.abc.Iterable[typing.ForwardRef("str")],
        ]
        signature = inspect.Signature([
            inspect.Parameter(
                "value", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
            )
        ])
        value = "a CAD domain value"
        result = _call_with_coercion(
            identity, {"value": value}, signature=signature, hint_target=False
        )
        self.assertIs(result, value)

    def test_union_coercion_preserves_iterable_domain_object(self):
        import collections.abc
        import typing
        from flow_runtime import _coerce_annotation

        class IterableShape:
            def __iter__(self):
                return iter((1, 2, 3))

            def __len__(self):
                return 3

            def __getitem__(self, index):
                return (1, 2, 3)[index]

        shape_type = typing.TypeVar("ShapeType", bound=IterableShape)
        annotation = typing.Union[
            shape_type, collections.abc.Iterable[IterableShape]
        ]
        shape = IterableShape()
        self.assertIs(_coerce_annotation(shape, annotation), shape)

        values = [IterableShape(), IterableShape()]
        converted = _coerce_annotation(values, annotation)
        self.assertIsInstance(converted, list)
        self.assertEqual(converted, values)

    def test_annotation_metadata_does_not_treat_iterable_domain_values_as_lists(self):
        from flow_runtime import _annotation_metadata

        class IterableDomainValue:
            def __iter__(self):
                return iter(())

            def __len__(self):
                return 0

            def __getitem__(self, index):
                raise IndexError(index)

        class CustomList(list):
            pass

        self.assertNotIn("data_structure", _annotation_metadata(IterableDomainValue))
        self.assertEqual(
            _annotation_metadata(CustomList)["data_structure"], "list"
        )

    def test_discovered_operator_preserves_nonstandard_operand_parameter_name(self):
        import inspect
        from flow_runtime import _invoke_discovered_operator_callable

        def edge_matmul(self, position):
            return ("position", position)

        result = _invoke_discovered_operator_callable(
            lambda mapped: edge_matmul(mapped["self"], mapped["position"]),
            "__matmul__",
            {"self": object(), "position": 0.375},
            inspect.signature(edge_matmul),
        )
        self.assertEqual(result, ("position", 0.375))

    def test_discovered_operator_falls_back_only_after_not_implemented(self):
        import inspect
        from flow_runtime import _invoke_discovered_operator_callable

        class Left:
            def __mul__(self, other):
                return NotImplemented

        class Right:
            def __rmul__(self, other):
                return "reflected result"

        def multiply(self, other):
            return self.__mul__(other)

        left, right = Left(), Right()
        result = _invoke_discovered_operator_callable(
            lambda mapped: multiply(mapped["self"], mapped["other"]),
            "__mul__",
            {"self": left, "other": right},
            inspect.signature(multiply),
        )
        self.assertEqual(result, "reflected result")

    def test_kernel_timeout_escalates_from_terminate_to_kill(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from test_upstream_kernel import stop_worker

        class StubbornWorker:
            def __init__(self):
                self.alive = True
                self.terminated = False
                self.killed = False

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True

            def join(self, timeout=None):
                # Simulate a native call that ignores SIGTERM.
                return None

            def kill(self):
                self.killed = True
                self.alive = False

        worker = StubbornWorker()
        stop_worker(worker)
        self.assertTrue(worker.terminated)
        self.assertTrue(worker.killed)
        self.assertFalse(worker.is_alive())

    def test_context_manager_translation_uses_catalog_metadata(self):
        manager = self.catalog.resolve_type("BuildPart")
        self.assertTrue(manager["supports_context_manager"])
        source = """
from build123d import BuildPart, Box
with BuildPart() as part:
    Box(10, 10, 10)
"""
        graph = runtime.import_python_code(source)
        scopes = graph.get("execution_scopes", [])
        self.assertTrue(scopes)
        self.assertEqual(scopes[0]["name"], "BuildPart")
        self.assertEqual(scopes[0]["module"], manager["module"])
        self.assertIsNone(runtime.b3d)

    def test_plane_class_constant_translates_without_kernel(self):
        plane = self.catalog.resolve_class_value("Plane", "XY")
        self.assertEqual(plane.get("__flow_object__"), "Plane")
        graph = runtime.import_python_code("""
from build123d import BuildSketch, Circle, Plane
with BuildSketch(Plane.XY):
    Circle(5)
""")
        scopes = graph.get("execution_scopes", [])
        self.assertTrue(scopes)
        self.assertEqual(
            scopes[0]["args"][0]["__flow_object__"],
            "Plane",
        )
        self.assertIn("origin", scopes[0]["args"][0])
        self.assertIsNone(runtime.b3d)

    def test_enum_arguments_translate_from_discovered_members(self):
        align = self.catalog.resolve_type("Align")
        self.assertTrue(align["is_enum"])
        graph = runtime.import_python_code("""
from build123d import Box, Align
box = Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
""")
        box_node = next(
            node for node in graph["nodes"]
            if node.get("data", {}).get("definition", {}).get("id", "").rsplit(".overload-", 1)[0].endswith(".Box")
        )
        self.assertEqual(
            box_node["data"]["params"]["align"],
            {"__flow_tuple__": ["CENTER", "CENTER", "MIN"]},
        )

    def test_overloaded_constructor_signatures_are_discovered(self):
        overloaded_types = [
            record for record in self.catalog.types.values()
            if sum(
                ".overload-" in node_id
                for node_id in record.get("constructor_node_ids", [])
            ) > 1
        ]
        self.assertTrue(overloaded_types, "No overloaded public constructors were discovered")
        for record in overloaded_types:
            self.assertTrue(
                set(record["constructor_node_ids"]).issubset(self.catalog.nodes),
                record["id"],
            )

    def test_class_level_api_call_translates_without_kernel(self):
        graph = runtime.import_python_code("""
from build123d import Wire
wire = Wire.make_circle(5)
""")
        identities = [
            node.get("data", {}).get("definition", {}).get("id", "")
            for node in graph["nodes"]
        ]
        self.assertTrue(
            any(".make_circle" in identity and
                (".static." in identity or ".classmethod." in identity)
                for identity in identities),
            identities,
        )

    def test_all_discovered_type_node_references_resolve(self):
        for identity, record in self.catalog.types.items():
            references = (
                record.get("constructor_node_ids", [])
                + record.get("member_node_ids", [])
            )
            self.assertTrue(
                set(references).issubset(self.catalog.nodes),
                "{} contains dangling node references".format(identity),
            )


if __name__ == "__main__":
    unittest.main()