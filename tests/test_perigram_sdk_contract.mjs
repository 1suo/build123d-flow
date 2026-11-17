import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { readFile } from 'node:fs/promises';

const root = process.env.PERIGRAM_REPO;
if (!root) throw new Error('PERIGRAM_REPO must point to a Perigram checkout');

const sdk = await import(pathToFileURL(path.join(root, 'packages/plugin-sdk/src/node-contract.js')));
const listingSdk = await import(pathToFileURL(path.join(root, 'packages/plugin-sdk/src/listing.js')));
const graph = await import(pathToFileURL(path.join(root, 'packages/core/src/graph.js')));
const proceduralEvaluator = await import(pathToFileURL(path.join(root, 'packages/core/src/procedural-evaluator.js')));
const graphContracts = await import(pathToFileURL(path.join(root, 'packages/core/src/graph.js')));
const { usePluginRuntime } = await import(pathToFileURL(path.join(root, 'composables/usePluginRuntime.js')));
const listing = JSON.parse(await readFile('perigram-listing.json', 'utf8'));
const parsedListing = listingSdk.parseListing(listing);
assert.equal(parsedListing.pluginId, 'build123d-flow');
assert.equal(parsedListing.runtimes[0].id, 'build123d-pyodide');
assert.equal(parsedListing.runtimes[0].entry, '/perigram-runtime.js');
assert.equal(parsedListing.surfaces[0].id, 'build123d-cad-preview');
assert.equal(parsedListing.surfaces[0].module, '/preview/build123d-preview.js');
assert.equal(parsedListing.previewChannels[0].surface, 'build123d-cad-preview');


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
    "core.list.length": {
        "id": "core.list.length",
        "source": "flow",
        "name": "List Length",
        "inputs": [{"name": "items", "type": "Any", "data_structure": "list"}],
        "outputs": [{"name": "result", "type": "Number"}],
    },
    "build123d.VarArgs": {
        "id": "build123d.VarArgs",
        "name": "Varargs operation",
        "inputs": [
            {"name": "*args", "type": "Any"},
            {"name": "**kwargs", "type": "Any"},
        ],
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
assert.equal(payload.definitions.length, 7);
const scopeOperations = payload.definitions.filter(item =>
  item.id === 'core.python.scope.binding' || item.id === 'core.python.scope.result'
);
assert.equal(scopeOperations.length, 2);
for (const definition of payload.definitions) {
  assert.deepEqual(sdk.validateNodeDefinition(definition), []);
}
assert.deepEqual(graph.validateGraphDocument(payload.document), []);
const boxDefinition = payload.definitions.find(item => item.id === 'build123d.Box');
assert.equal(boxDefinition.preview.renderer, 'cad-mesh');
const varargs = payload.definitions.find(item => item.id === 'build123d.VarArgs');
assert.deepEqual(varargs.configuration.pythonPortNames.inputs, {
  varargs_args: '*args',
  kwargs_kwargs: '**kwargs',
});
assert.equal(payload.document.edges[0].source.port, 'result');
assert.equal(payload.document.edges[0].target.port, 'shapes');
console.log('All sample provider definitions and canonical graph document pass actual Perigram validators');

const inputOperation = graphContracts.GRAPH_INPUT_OPERATION;
const outputOperation = graphContracts.GRAPH_OUTPUT_OPERATION;
const executionGraph = {
  format: 'perigram.graph',
  version: 1,
  id: 'graph.execution-context',
  revision: 'rev-1',
  interface: {
    inputs: [{ id: 'number', contract: { shape: 'item', semantic: { base: 'number' } } }],
    outputs: [{ id: 'result', contract: { shape: 'item', semantic: { base: 'number' } } }]
  },
  nodes: [
    { id: 'in', operation: { provider: 'perigram.core', id: inputOperation, version: '1' }, config: { interfaceId: 'number' } },
    { id: 'first', operation: { provider: 'test.provider', id: 'double', version: '1' }, config: {} },
    { id: 'second', operation: { provider: 'test.provider', id: 'double', version: '1' }, config: {} },
    { id: 'out', operation: { provider: 'perigram.core', id: outputOperation, version: '1' }, config: { interfaceId: 'result' } }
  ],
  edges: [
    { id: 'e1', source: { node: 'in', port: 'value' }, target: { node: 'first', port: 'value' } },
    { id: 'e2', source: { node: 'first', port: 'result' }, target: { node: 'second', port: 'value' } },
    { id: 'e3', source: { node: 'second', port: 'result' }, target: { node: 'out', port: 'value' } }
  ]
};
const executionContexts = [];
const finalizedExecutionIds = [];
const executionResult = await proceduralEvaluator.evaluateProceduralGraph({
  graph: executionGraph,
  inputBindings: { number: 3 },
  context: { finalizeExecution: async executionId => finalizedExecutionIds.push(executionId) },
  resolveNodeDefinition: () => ({
    provider: 'test.provider',
    id: 'double',
    version: '1',
    inputs: [{ id: 'value', required: true }],
    outputs: [{ id: 'result', required: true }],
    execution: { kind: 'pure' }
  }),
  executeNode: (_definition, { inputs, context }) => {
    executionContexts.push(context);
    return { result: inputs.value * 2 };
  }
});
assert.deepEqual(executionResult.outputs, { result: 12 });
assert.equal(executionContexts.length, 2);
assert.match(executionContexts[0].executionId, /^graph-preview-/);
assert.equal(executionContexts[1].executionId, executionContexts[0].executionId);
assert.deepEqual(finalizedExecutionIds, [executionContexts[0].executionId]);

class ContractWorker {
  constructor() {
    this.messages = [];
    this.onmessage = null;
    this.onerror = null;
    this.terminated = false;
  }
  postMessage(message) {
    this.messages.push(message);
    if (message.type === 'initialize') {
      queueMicrotask(() => this.onmessage?.({ data: { type: 'ready', providers: [] } }));
    }
    if (message.type === 'invoke') {
      const input = structuredClone(message.input);
      this.invocation = input;
      queueMicrotask(() => this.onmessage?.({
        data: { type: 'result', invocationId: message.invocationId, value: input }
      }));
    }
    if (message.type === 'clear-execution') {
      queueMicrotask(() => this.onmessage?.({
        data: { type: 'result', invocationId: message.invocationId, value: true }
      }));
    }
  }
  terminate() { this.terminated = true; }
}
const contractWorker = new ContractWorker();
const pluginRuntime = usePluginRuntime({ createWorker: () => contractWorker });
const hostInvocation = {
  node: { id: 'node-1' },
  config: { graphId: 'graph.execution-context' },
  inputs: { width: 4 },
  context: {
    graphId: 'graph.execution-context',
    executionId: executionContexts[0].executionId,
    readSet: {
      recordInstance() {},
      recordRelation() {},
      recordQuery() {},
      toJSON() { return {}; }
    }
  },
  mode: 'preview'
};
await pluginRuntime.execute(
  { organizationId: 'org-1', pluginId: 'build123d-flow', version: '0.1.0' },
  { id: 'build123d-pyodide', version: '314.0.7-r18', entryUrl: '/perigram-runtime.js', targets: ['browser'] },
  'build123d.Box',
  hostInvocation,
  { execution: { kind: 'pure', runtime: 'build123d-pyodide', targets: ['browser'], capabilities: [] } }
);
assert.deepEqual(contractWorker.invocation.context, {
  graphId: 'graph.execution-context',
  executionId: executionContexts[0].executionId
});
assert.equal(contractWorker.invocation.inputs.width, 4);
assert.deepEqual(await pluginRuntime.finalizeExecution(executionContexts[0].executionId), [true]);
assert.equal(contractWorker.messages.some(message =>
  message.type === 'clear-execution' && message.executionId === executionContexts[0].executionId
), true);
pluginRuntime.dispose();
assert.equal(contractWorker.terminated, true);

console.log('Perigram procedural preview and plugin worker context contracts passed');
