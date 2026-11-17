import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = process.env.PERIGRAM_REPO;
if (!root) throw new Error('PERIGRAM_REPO must point to a Perigram checkout');

const sdk = await import(pathToFileURL(path.join(root, 'packages/plugin-sdk/src/node-contract.js')));
const graph = await import(pathToFileURL(path.join(root, 'packages/core/src/graph.js')));

const python = spawnSync('python', ['-c', `
import json
from perigram_graph import provider_definitions, translate_graph
catalog = {
    "build123d.Box": {
        "id": "build123d.Box",
        "name": "Box",
        "inputs": [
            {"name": "length", "type": "Number", "required": True},
            {"name": "plane", "type": "Plane", "type_id": "build123d.geometry.Plane", "type_provider": "build123d", "required": False},
        ],
        "outputs": [{"name": "result", "type": "Solid", "type_id": "build123d.topology.three_d.Solid", "type_provider": "build123d"}],
    },
    "core.python.select": {
        "id": "core.python.select",
        "name": "Python conditional value",
        "source": "python",
        "inputs": [{"name": "value", "type": "Any", "data_structure": "tree"}],
        "outputs": [{"name": "result", "type": "Any"}],
    },
    "build123d.ShapeList": {
        "id": "build123d.ShapeList",
        "name": "Shape list operation",
        "inputs": [{"name": "shapes", "type": "Shape", "type_id": "build123d.topology.shape_core.Shape", "type_provider": "build123d", "data_structure": "list"}],
        "outputs": [{"name": "result", "type": "Any"}],
    },
}
definitions = provider_definitions(catalog)
document = translate_graph("Box(1, 2, 3)", {
    "nodes": [
        {
            "id": "node-box",
            "position": {"x": 0, "y": 0},
            "data": {
                "definition": catalog["build123d.Box"],
                "params": {"length": 1},
                "execution_scope_path": [],
            },
        },
        {
            "id": "node-next",
            "position": {"x": 240, "y": 0},
            "data": {
                "definition": catalog["build123d.ShapeList"],
                "params": {},
                "execution_scope_path": [],
            },
        },
    ],
    "edges": [{
        "id": "edge-box-next",
        "source": "node-box",
        "target": "node-next",
        "sourceHandle": "result",
        "targetHandle": "shapes",
    }],
    "execution_scopes": [],
})
print(json.dumps({"definitions": definitions, "document": document}, allow_nan=False))
`], {
  encoding: 'utf8',
  env: process.env,
});
assert.equal(python.status, 0, python.stderr || python.stdout);
const payload = JSON.parse(python.stdout);
assert.equal(payload.definitions.length, 3);
for (const definition of payload.definitions) {
  assert.deepEqual(sdk.validateNodeDefinition(definition), []);
}
assert.deepEqual(graph.validateGraphDocument(payload.document), []);
assert.equal(payload.document.edges[0].source.port, 'result');
assert.equal(payload.document.edges[0].target.port, 'shapes');
console.log('All sample provider definitions and canonical graph document pass actual Perigram validators');
