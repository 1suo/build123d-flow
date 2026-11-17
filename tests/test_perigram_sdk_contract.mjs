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
from perigram_graph import normalize_node_definition, translate_graph
definition = normalize_node_definition({
    "id": "build123d.Box",
    "name": "Box",
    "inputs": [{"name": "length", "type": "Number", "required": True}],
    "outputs": [{"name": "result", "type": "Any"}],
})
document = translate_graph("Box(1, 2, 3)", {
    "nodes": [{
        "id": "node-box",
        "position": {"x": 0, "y": 0},
        "data": {
            "definition": {"id": "build123d.Box", "source": "build123d"},
            "params": {"length": 1},
            "execution_scope_path": [],
        },
    }],
    "edges": [],
    "execution_scopes": [],
})
print(json.dumps({"definition": definition, "document": document}, allow_nan=False))
`], {
  encoding: 'utf8',
  env: process.env,
});
assert.equal(python.status, 0, python.stderr || python.stdout);
const payload = JSON.parse(python.stdout);
assert.deepEqual(sdk.validateNodeDefinition(payload.definition), []);
assert.deepEqual(graph.validateGraphDocument(payload.document), []);
console.log('Perigram SDK node definition and core graph document contracts passed');
