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

    def test_non_cad_nominal_types_do_not_default_to_build123d(self):
        descriptor = semantic_descriptor("my_package.CustomValue")
        self.assertEqual(descriptor, {
            "kind": "nominal",
            "provider": "perigram.python",
            "typeId": "my_package.CustomValue",
        })

    def test_graph_conversion_uses_canonical_document_and_provider_qualified_operations(self):
        result = translate_graph("Box(1, 2, 3)", {
            "nodes": [{
                "id": "n1", "type": "api", "position": {"x": 1, "y": 2},
                "data": {
                    "definition": {"id": "build123d.Box", "source": "build123d"},
                    "params": {"length": 1},
                    "execution_scope_path": [],
                },
            }],
            "edges": [],
            "execution_scopes": [],
        })
        self.assertEqual(result["format"], "perigram.graph")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["nodes"][0]["operation"], {
            "provider": "build123d", "id": "build123d.Box", "version": "1"
        })
        self.assertEqual(result["metadata"]["source"]["language"], "python")

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
