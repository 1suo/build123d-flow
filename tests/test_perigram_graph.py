import unittest

from perigram_graph import (
    GRAPH_FORMAT,
    PerigramGraphError,
    normalize_node_definition,
    semantic_descriptor,
    translate_graph,
    transport_shape,
)


class PerigramGraphAdapterTests(unittest.TestCase):
    def test_uses_perigram_core_graph_document_identity(self):
        self.assertEqual(GRAPH_FORMAT, "perigram.graph")

    def test_any_is_semantic_any_not_a_geometry_exclusion(self):
        self.assertEqual(semantic_descriptor("Any"), {"kind": "any"})

    def test_semantic_identity_and_transport_shape_are_independent(self):
        socket = {"name": "shapes", "type": "build123d.Solid", "data_structure": "tree"}
        self.assertEqual(transport_shape(socket), "tree")
        definition = normalize_node_definition({
            "id": "build123d.Solid.union",
            "name": "Union",
            "inputs": [socket],
            "outputs": [{"name": "result", "type": "Any"}],
        })
        self.assertEqual(definition["inputs"][0]["shape"], "tree")
        self.assertEqual(definition["inputs"][0]["semantic"]["typeId"], "build123d.Solid")
        self.assertEqual(definition["outputs"][0]["semantic"], {"kind": "any"})
        self.assertEqual(definition["execution"]["targets"], ["browser"])
        self.assertEqual(definition["execution"]["runtime"], "build123d-pyodide")

    def test_discovered_type_identity_overrides_legacy_display_label(self):
        definition = normalize_node_definition({
            "id": "build123d.Box",
            "name": "Box",
            "inputs": [{
                "name": "profile",
                "type": "Solid",
                "type_id": "build123d.topology.three_d.Solid",
                "type_provider": "build123d",
            }],
            "outputs": [],
        })
        self.assertEqual(definition["inputs"][0]["semantic"], {
            "kind": "nominal",
            "provider": "build123d",
            "typeId": "build123d.topology.three_d.Solid",
        })

    def test_python_varargs_and_private_names_get_stable_port_ids(self):
        definition = normalize_node_definition({
            "id": "build123d.example",
            "source": "build123d",
            "inputs": [
                {"name": "*args", "type": "Any"},
                {"name": "**kwargs", "type": "Any"},
                {"name": "_private", "type": "Any"},
            ],
            "outputs": [{"name": "result", "type": "Any"}],
        })
        self.assertEqual(
            [port["id"] for port in definition["inputs"]],
            ["varargs_args", "kwargs_kwargs", "p__private"],
        )
        self.assertEqual(definition["configuration"]["pythonPortNames"]["inputs"], {
            "varargs_args": "*args",
            "kwargs_kwargs": "**kwargs",
            "p__private": "_private",
        })

    def test_flow_and_python_primitives_use_python_provider(self):
        definition = normalize_node_definition({
            "id": "core.list.length",
            "source": "flow",
            "name": "List Length",
            "inputs": [{"name": "items", "type": "Any", "data_structure": "list"}],
            "outputs": [{"name": "result", "type": "Number"}],
        })
        self.assertEqual(definition["provider"], "perigram.python")

    def test_non_cad_nominal_types_do_not_default_to_build123d(self):
        descriptor = semantic_descriptor("my_package.CustomValue")
        self.assertEqual(descriptor, {
            "kind": "nominal",
            "provider": "perigram.python",
            "typeId": "my_package.CustomValue",
        })

    def test_type_labels_do_not_implicitly_define_transport_shape(self):
        self.assertEqual(transport_shape({"name": "values", "type": "List"}), "item")

    def test_python_collection_structures_map_to_perigram_shapes(self):
        self.assertEqual(transport_shape({
            "name": "values", "type": "Any", "data_structure": "set"
        }), "list")
        self.assertEqual(transport_shape({
            "name": "values", "type": "Any", "data_structure": "dict"
        }), "map")
        self.assertEqual(transport_shape({
            "name": "values", "type": "Any", "data_access": "map"
        }), "map")

    def test_graph_conversion_uses_canonical_document_and_provider_qualified_operations(self):
        result = translate_graph("Box(1, 2, 3)", {
            "nodes": [{
                "id": "n1", "type": "api", "position": {"x": 1, "y": 2},
                "data": {
                    "definition": {"id": "build123d.Box", "source": "build123d"},
                    "params": {"length": 1},
                    "execution_scope_path": ["scope-1"],
                },
            }],
            "edges": [],
            "execution_scopes": [{"id": "scope-1", "module": "build123d", "name": "BuildPart"}],
        })
        self.assertEqual(result["format"], "perigram.graph")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["nodes"][0]["operation"], {
            "provider": "build123d", "id": "build123d.Box", "version": "1"
        })
        self.assertEqual(result["metadata"]["source"]["language"], "python")
        self.assertEqual(result["nodes"][0]["config"]["executionScopePath"], ["scope-1"])
        self.assertEqual(result["nodes"][0]["config"]["executionScopes"], [
            {"id": "scope-1", "module": "build123d", "name": "BuildPart"}
        ])

    def test_graph_config_preserves_input_source_and_loop_metadata(self):
        result = translate_graph("source -> target", {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "definition": {"id": "build123d.Box"},
                        "params": {},
                        "python_loop_sources": [
                            {"node_id": "loop-source", "output_name": "result", "matching": "shortest"}
                        ],
                    },
                },
                {
                    "id": "target",
                    "data": {
                        "definition": {"id": "build123d.Shape.translate"},
                        "params": {},
                    },
                },
            ],
            "edges": [{
                "id": "wire-1",
                "source": "source",
                "target": "target",
                "sourceHandle": "result",
                "targetHandle": "self",
            }],
            "execution_scopes": [],
        })
        target = next(node for node in result["nodes"] if node["id"] == "target")
        self.assertEqual(target["config"]["inputSources"]["self"], [{
            "nodeId": "source",
            "outputId": "result",
            "pythonLoopSources": [
                {"node_id": "loop-source", "output_name": "result", "matching": "shortest"}
            ],
        }])

    def test_graph_edges_and_parameters_use_normalized_port_ids(self):
        result = translate_graph("source -> target", {
            "nodes": [
                {
                    "id": "source",
                    "data": {"definition": {"id": "build123d.Source"}, "params": {}},
                },
                {
                    "id": "target",
                    "data": {
                        "definition": {
                            "id": "build123d.VarArgs",
                            "inputs": [
                                {"name": "*args", "type": "Any"},
                                {"name": "**kwargs", "type": "Any"},
                            ],
                        },
                        "params": {"*args": [1, 2], "**kwargs": {"x": 3}},
                    },
                },
            ],
            "edges": [{
                "id": "wire",
                "source": "source",
                "target": "target",
                "sourceHandle": "_private",
                "targetHandle": "*args",
            }],
            "execution_scopes": [],
        })
        target = next(node for node in result["nodes"] if node["id"] == "target")
        self.assertEqual(target["config"]["parameters"], {
            "varargs_args": [1, 2],
            "kwargs_kwargs": {"x": 3},
        })
        self.assertEqual(target["config"]["pythonPortNames"]["inputs"], {
            "varargs_args": "*args",
            "kwargs_kwargs": "**kwargs",
        })
        self.assertEqual(result["edges"][0]["source"]["port"], "p__private")
        self.assertEqual(result["edges"][0]["target"]["port"], "varargs_args")

    def test_edges_require_existing_nodes_and_named_ports(self):
        with self.assertRaises(PerigramGraphError):
            translate_graph("x", {
                "nodes": [{
                    "id": "n1",
                    "data": {"definition": {"id": "build123d.Box"}},
                }],
                "edges": [{
                    "id": "e1", "source": "missing", "target": "n1",
                    "sourceHandle": "result", "targetHandle": "shape",
                }],
            })

    def test_non_json_native_values_fail_closed(self):
        with self.assertRaises(PerigramGraphError):
            normalize_node_definition({
                "id": "build123d.BadDefault",
                "inputs": [{"name": "value", "type": "Any", "default": object()}],
                "outputs": [],
            })

    def test_non_finite_numbers_fail_closed(self):
        with self.assertRaises(PerigramGraphError):
            translate_graph("x", {
                "nodes": [{
                    "id": "n1", "data": {
                        "definition": {"id": "build123d.Bad"},
                        "params": {"value": float("nan")},
                    }
                }], "edges": [],
            })

    def test_missing_operation_identity_fails_closed(self):
        with self.assertRaises(PerigramGraphError):
            translate_graph("x", {"nodes": [{"id": "n", "data": {}}], "edges": []})


if __name__ == "__main__":
    unittest.main()
