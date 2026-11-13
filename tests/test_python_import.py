"""Structural checks for the conservative Python-to-graph translator."""

import ast
import copy
import inspect
import types
import unittest
from pathlib import Path


RUNTIME_PATH = Path(__file__).parents[1] / "public" / "python" / "flow_runtime.py"


class PythonImportSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
        cls.importer = next(
            node for node in cls.module.body
            if isinstance(node, ast.FunctionDef) and node.name == "import_python_code"
        )

    def test_importer_uses_builtin_ast_directly(self):
        calls = [
            node for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertTrue(any(
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ast"
            and node.func.attr == "parse"
            for node in calls
        ))
        self.assertFalse(any(
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("python_importer_")
            for node in ast.walk(self.importer)
        ))

    def test_importer_keeps_catalog_resolution_local(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("resolve_member", functions)
        source = ast.unparse(functions["resolve_member"])
        self.assertIn("variants", source)
        self.assertIn("receiver_type", source)

    def test_importer_never_executes_or_evaluates_source(self):
        forbidden = {"exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id for node in ast.walk(self.importer)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(forbidden.intersection(calls))

    def test_member_resolution_matches_catalog_method_ids_not_display_names(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        importer = functions["import_python_code"]
        resolver = next(
            node for node in ast.walk(importer)
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_member"
        )
        source = ast.unparse(resolver)
        self.assertIn("candidate_id.rsplit", source)
        self.assertIn("member_name", source)
        self.assertNotIn("candidate.get('name')", source)
        self.assertNotIn('candidate.get("name")', source)

    def test_build123d_vector_class_matches_vector3_socket_type(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        source = ast.unparse(functions["_type_name"])
        self.assertIn("'Vector': 'Vector3'", source)

    def test_shape_subclasses_keep_distinct_receiver_types(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        source = ast.unparse(functions["_type_name"])
        self.assertIn("'Vertex'", source)
        self.assertIn('return shape_name', source)

    def test_unmatched_receiver_resolves_only_a_unique_discovered_method(self):
        resolver = next(
            node for node in self.importer.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_member"
        )

        def resolve(definitions, receiver_type):
            module = ast.Module(body=[copy.deepcopy(resolver)], type_ignores=[])
            namespace = {
                "definitions": definitions,
                "b3d": types.SimpleNamespace(),
                "inspect": inspect,
            }
            exec(compile(module, "<resolve_member_test>", "exec"), namespace)
            return namespace["resolve_member"]("group_by", receiver_type)

        def method(receiver_type):
            return {
                "inputs": [{"name": "self", "type": receiver_type}],
            }

        unique_id = "build123d.topology.ShapeList.group_by"
        self.assertEqual(
            resolve({unique_id: method("ShapeList")}, "UnrecognizedCollection"),
            (unique_id, (unique_id,)),
        )

        other_id = "build123d.other.OtherList.group_by"
        self.assertEqual(
            resolve({
                unique_id: method("ShapeList"),
                other_id: method("OtherList"),
            }, "UnrecognizedCollection"),
            (None, tuple(sorted((unique_id, other_id)))),
        )

        self.assertEqual(
            resolve({
                unique_id: method("ShapeList"),
                other_id: method("OtherList"),
            }, "ShapeList"),
            (unique_id, (unique_id,)),
        )

    def test_ambiguous_properties_defer_to_native_python_lookup(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        importer = functions["import_python_code"]
        expression = next(
            node for node in ast.walk(importer)
            if isinstance(node, ast.FunctionDef) and node.name == "expression"
        )
        source = ast.unparse(expression)
        self.assertIn("_PYTHON_ATTRIBUTE_ID", source)
        self.assertIn("node.attr", source)
        self.assertIn("[('value', receiver[1], receiver[2])]", source)
        self.assertIn("return ('ref', created, 'result')", source)
        helper = ast.unparse(functions["_python_getattr"])
        self.assertIn("getattr(value, name)", helper)

        exporter = ast.unparse(functions["export_python_code"])
        self.assertIn("definition['id'] == 'core.python.attribute'", exporter)
        self.assertIn("getattr({}, {})", exporter)

    def test_catalog_discovers_typed_build123d_collection_members(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        type_name = ast.unparse(functions["_type_name"])
        discovery = ast.unparse(functions["discover"])
        self.assertIn("getattr(origin", type_name)
        self.assertIn("__module__", type_name)
        self.assertIn("annotation.__name__", type_name)
        self.assertIn("'Vector': 'Vector3'", type_name)
        member_loop = next(
            node for node in ast.walk(functions["discover"])
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "cls"
            and isinstance(node.iter, ast.Name)
            and node.iter.id == "api_classes"
        )
        member_loop_source = ast.unparse(member_loop)
        self.assertNotIn("_candidate_rejection", member_loop_source)
        self.assertIn("for owner in getattr(cls, '__mro__'", member_loop_source)

    def test_graph_evaluator_binds_discovered_instance_methods(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        evaluator = ast.unparse(functions["evaluate_graph_json"])
        self.assertIn("bound_method = getattr(receiver_value, method_name)", evaluator)
        self.assertIn("arguments = {name: value for name, value in mapped.items()", evaluator)
        self.assertIn("bound_signature", evaluator)

    def test_graph_evaluator_restores_serialized_value_receivers(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        evaluator = ast.unparse(functions["evaluate_graph_json"])
        receiver = ast.unparse(functions["_coerce_graph_receiver"])
        self.assertIn("_coerce_graph_receiver", evaluator)
        self.assertIn("'Vector3': b3d.Vector", receiver)
        self.assertIn("'Location': b3d.Location", receiver)
        self.assertIn("_coerce_annotation(value, annotation)", receiver)

    def test_importer_serializes_axis_class_constants(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        source = ast.unparse(functions["expression"])
        serializer = ast.unparse(functions["literal_json_value"])
        self.assertIn("b3d.Axis", source)
        self.assertIn("return ('literal', value)", source)
        self.assertIn("isinstance(value, b3d.Axis)", serializer)
        self.assertIn("value.position", serializer)
        self.assertIn("value.direction", serializer)

    def test_importer_evaluates_unary_operators_on_literals(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        source = ast.unparse(functions["expression"])
        unary = next(
            node for node in ast.walk(functions["expression"])
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Subscript)
            and isinstance(node.test.left.value, ast.Name)
            and node.test.left.value.id == "operand"
        )
        self.assertIsInstance(unary.test.comparators[0], ast.Constant)
        self.assertEqual(unary.test.comparators[0].value, "literal")
        self.assertIn("apply_operator(materialize_literal(operand[1]))", source)
        self.assertIn("operator.neg", source)
        self.assertIn("operator.pos", source)
        self.assertIn("operator.invert", source)

    def test_context_manager_arguments_share_the_expression_resolver(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("scope_argument", functions)
        self.assertIn("expression(node)", ast.unparse(functions["scope_expression"]))
        self.assertIn("scope_expression(argument)", ast.unparse(functions["resolve_context_manager"]))
        process = ast.unparse(functions["process_statements"])
        self.assertIn("scope_reference_ids([args, kwargs])", process)
        self.assertIn("binding_node = add_scope_node", process)

    def test_graph_plan_orders_scope_arguments_and_scope_bodies(self):
        graph_plan = next(
            node for node in self.module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_graph_plan"
        )
        source = ast.unparse(graph_plan)
        self.assertIn("scope_dependencies", source)
        self.assertIn("execution_scope_path", source)
        self.assertIn("scope_bindings", source)
        self.assertIn("add_ordering_dependency", source)

    def test_expression_resolver_keeps_typed_values_until_serialization(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        expression = ast.unparse(functions["expression"])
        serializer = ast.unparse(functions["literal_json_value"])
        self.assertIn("__flow_object__", expression)
        self.assertIn("materialize_literal", expression)
        self.assertIn("literal_json_value(value)", expression)
        self.assertIn("b3d.Plane", serializer)
        self.assertIn("b3d.Location", serializer)
        self.assertIn("b3d.Vector", serializer)

    def test_execution_scopes_preserve_python_tuple_values(self):
        functions = {
            node.name: node for node in self.module.body
            if isinstance(node, ast.FunctionDef)
        }
        encoder = ast.unparse(functions["encode_scope_value"])
        runtime = ast.unparse(self.module)
        self.assertIn('"__flow_tuple__"', encoder)
        self.assertIn('"__flow_tuple__"', runtime)
        self.assertIn("tuple(restore_scope_value(item)", runtime)

    def test_binary_expression_supports_every_python_binary_operator(self):
        functions = {
            node.name: node for node in self.importer.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        source = ast.unparse(functions["expression"])
        for operator_name in (
            "MatMult", "LShift", "RShift", "operator.matmul",
            "operator.lshift", "operator.rshift",
        ):
            self.assertIn(operator_name, source)

    def test_importer_reports_unsupported_statements_with_location(self):
        source = ast.unparse(self.importer)
        self.assertIn("Unsupported statement", source)
        self.assertIn("lineno", source)

    def test_importer_builds_native_graph_records(self):
        source = ast.unparse(self.importer)
        for required in ("graph_nodes", "graph_edges", "'position'", "'definition'", "'params'"):
            self.assertIn(required, source)

    def test_context_managers_are_preserved_as_execution_scopes(self):
        source = ast.unparse(self.importer)
        for required in ("ast.With", "execution_scopes", "execution_scope_path",
                         "scope_binding", "scope_result", "resolve_context_manager"):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
