import unittest

from perigram_graph import provider_definitions, translate_graph


class PerigramScopeContractTests(unittest.TestCase):
    def test_internal_scope_operations_are_registered_as_preview_safe_browser_nodes(self):
        definitions = provider_definitions({})
        by_id = {definition["id"]: definition for definition in definitions}
        for operation_id in (
            "core.python.scope.binding",
            "core.python.scope.result",
        ):
            definition = by_id[operation_id]
            self.assertEqual(definition["provider"], "perigram.python")
            self.assertEqual(definition["execution"]["kind"], "read")
            self.assertTrue(definition["configuration"]["scopeLifecycle"])
            self.assertEqual(definition["execution"]["runtime"], "build123d-pyodide")
            self.assertEqual(definition["execution"]["targets"], ["browser"])
            self.assertEqual([port["id"] for port in definition["outputs"]], ["result"])

    def test_scope_ids_and_graph_session_are_carried_to_each_node_invocation(self):
        scope = {
            "id": "scope-1",
            "module": "build123d.build_part",
            "name": "BuildPart",
            "args": [],
            "kwargs": {},
            "has_binding": True,
            "result_attribute": "part",
        }
        translated = {
            "execution_scopes": [scope],
            "nodes": [
                {
                    "id": "scope-binding",
                    "data": {
                        "definition": {
                            "id": "core.python.scope.binding",
                            "source": "python",
                            "version": "1",
                            "inputs": [],
                            "outputs": [{"name": "result", "type": "Any"}],
                        },
                        "scope_binding": "scope-1",
                        "scope_dependencies": ["plane-node"],
                        "execution_scope_path": ["scope-1"],
                    },
                },
                {
                    "id": "body-node",
                    "data": {
                        "definition": {
                            "id": "build123d.Box",
                            "source": "build123d",
                            "version": "1",
                            "inputs": [],
                            "outputs": [{"name": "result", "type": "Solid"}],
                        },
                        "execution_scope_path": ["scope-1"],
                    },
                },
                {
                    "id": "scope-result",
                    "data": {
                        "definition": {
                            "id": "core.python.scope.result",
                            "source": "python",
                            "version": "1",
                            "inputs": [],
                            "outputs": [{"name": "result", "type": "Any"}],
                        },
                        "scope_result": "scope-1",
                        "execution_scope_path": [],
                    },
                },
            ],
            "edges": [],
        }
        graph = translate_graph("with BuildPart(): pass", translated)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(nodes["scope-binding"]["config"]["scopeBinding"], "scope-1")
        self.assertEqual(nodes["scope-binding"]["config"]["scopeDependencies"], ["plane-node"])
        self.assertEqual(nodes["scope-binding"]["config"]["executionScopePath"], ["scope-1"])
        self.assertEqual(nodes["scope-result"]["config"]["scopeResult"], "scope-1")
        self.assertTrue(all(node["config"]["graphId"] == graph["id"] for node in nodes.values()))
        self.assertTrue(all(node["config"]["nodeId"] == node["id"] for node in nodes.values()))


if __name__ == "__main__":
    unittest.main()
