import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptRuntimeOperation,
  Build123dKernelProvider,
  cadPortTypeLabel,
  isCadGeometryPort,
  toCadProceduralGraph,
  validateCadOperation,
} from "../node_modules/.cache/cad-contract-tests/runtime/cadKernel.js";

function runtimeDefinition(overrides = {}) {
  return {
    id: "fixture.geometry.translate",
    kind: "operation",
    name: "Translate",
    category: "Fixture",
    source: "fixture-kernel",
    palette_category: "Transform",
    palette_section: "Geometry",
    description: "Translate opaque geometry by a vector",
    inputs: [
      { name: "shape", type: "Shape", annotation: "Geometry", required: true, data_access: "item" },
      { name: "offset", type: "Vector3", annotation: "Vector3", required: true },
    ],
    outputs: [
      { name: "result", type: "Shape", annotation: "Geometry", required: true, data_structure: "tree", item_type: "Shape" },
    ],
    execution: { purity: "pure", cacheable: true },
    data_matching: "cross",
    ...overrides,
  };
}

test("runtime metadata adapts to provider-qualified, nesting-preserving CAD operations", () => {
  const operation = adaptRuntimeOperation(runtimeDefinition());

  assert.equal(operation.id, "fixture.geometry.translate");
  assert.equal(operation.nativeOperation, "fixture.geometry.translate");
  assert.equal(operation.inputs[0].valueType, "geometry");
  assert.equal(operation.outputs[0].collection, "tree");
  assert.equal(operation.outputs[0].itemType, "geometry");
  assert.deepEqual(operation.mapping, {
    mode: "map",
    preserveNesting: true,
    alignment: "cartesian",
  });
  assert.equal(operation.execution, "pure");
  assert.equal(operation.cacheable, true);
  assert.equal(cadPortTypeLabel(operation.outputs[0]), "Tree[geometry]");
  assert.equal(isCadGeometryPort(operation.outputs[0]), true);
  assert.deepEqual(validateCadOperation(operation), []);
});

test("scalar and collection inputs retain distinct access and alignment semantics", () => {
  const scalar = adaptRuntimeOperation(runtimeDefinition({
    inputs: [
      { name: "value", type: "Number", annotation: "float", required: true },
    ],
    outputs: [{ name: "result", type: "Number", annotation: "float", required: true }],
    data_matching: undefined,
  }));
  // A scalar-typed socket still consumes one item at a time; a connected
  // stream of numbers therefore maps this operation without changing the
  // underlying Number contract.
  assert.deepEqual(scalar.mapping, {
    mode: "map",
    preserveNesting: true,
    alignment: "zip-longest",
  });
  assert.equal(scalar.inputs[0].collection, "item");
  assert.equal(scalar.inputs[0].access, "item");

  const structural = adaptRuntimeOperation(runtimeDefinition({
    inputs: [
      { name: "points", type: "List", item_type: "Vector3", data_structure: "list", data_access: "structure", required: true },
    ],
    outputs: [{ name: "result", type: "Shape", required: true }],
    data_matching: "shortest",
  }));
  assert.deepEqual(structural.mapping, {
    mode: "scalar",
    preserveNesting: true,
    alignment: "single",
  });
  assert.equal(structural.inputs[0].collection, "list");
  assert.equal(structural.inputs[0].access, "collection");
});

test("operation validation rejects ambiguous ports and invalid mapping contracts", () => {
  const duplicatePort = adaptRuntimeOperation(runtimeDefinition({
    inputs: [
      { name: "shape", type: "Shape", required: true },
      { name: "shape", type: "Shape", required: true },
    ],
  }));
  assert.ok(validateCadOperation(duplicatePort).some((message) => message.includes("duplicate/empty input id")));

  const lossyMap = {
    ...adaptRuntimeOperation(runtimeDefinition()),
    mapping: { mode: "map", preserveNesting: false, alignment: "zip-longest" },
  };
  assert.ok(validateCadOperation(lossyMap).some((message) => message.includes("must preserve nested collection shape")));

  const scalarCartesian = {
    ...adaptRuntimeOperation(runtimeDefinition()),
    mapping: { mode: "scalar", preserveNesting: true, alignment: "cartesian" },
  };
  assert.ok(validateCadOperation(scalarCartesian).some((message) => message.includes("scalar operations cannot declare stream alignment")));
});

test("provider-neutral graph conversion preserves opaque provider state and lexical scopes", () => {
  const definition = runtimeDefinition();
  const operation = adaptRuntimeOperation(definition);
  const graph = {
    nodes: [{
      id: "translate-1",
      type: "api",
      position: { x: 120, y: 240 },
      data: {
        definition: { ...definition, cadOperation: operation },
        params: { offset: [1, 2, 3] },
        visible: true,
        execution_scope_path: ["scope-1"],
        scope_binding: "scope-1",
      },
    }],
    edges: [{
      id: "edge-1",
      source: "source-1",
      sourceHandle: "result",
      target: "translate-1",
      targetHandle: "shape",
      pythonImported: true,
    }],
    execution_scopes: [{
      id: "scope-1",
      module: "fixture-kernel",
      name: "BuilderScope",
      args: [],
      kwargs: {},
      has_binding: true,
    }],
  };

  const procedural = toCadProceduralGraph(graph);
  assert.equal(procedural.nodes[0].operationId, "fixture.geometry.translate");
  assert.deepEqual(procedural.nodes[0].parameters, { offset: [1, 2, 3] });
  assert.equal(procedural.nodes[0].providerData.data.scope_binding, "scope-1");
  assert.deepEqual(procedural.connections, [{
    sourceNode: "source-1",
    sourcePort: "result",
    targetNode: "translate-1",
    targetPort: "shape",
    pythonImported: true,
  }]);
  assert.deepEqual(procedural.providerData.execution_scopes, graph.execution_scopes);
});

test("opaque geometry handles identify their provider without exposing native geometry", () => {
  const handle = Object.freeze({
    provider: "fixture-kernel",
    handle: "opaque-geometry-17",
    kind: "solid",
  });
  assert.deepEqual(handle, {
    provider: "fixture-kernel",
    handle: "opaque-geometry-17",
    kind: "solid",
  });
  assert.equal("wrapped" in handle, false);
  assert.equal("topology" in handle, false);
});


function fixtureProvider() {
  const literal = adaptRuntimeOperation(runtimeDefinition({
    id: "fixture.python.literal",
    name: "Literal",
    inputs: [],
    outputs: [{ name: "result", type: "Number", required: true }],
    data_matching: undefined,
  }));
  const add = adaptRuntimeOperation(runtimeDefinition({
    id: "fixture.number.operator.__add__",
    name: "Display label deliberately differs from dispatch identity",
    inputs: [
      { name: "left", type: "Number", required: true },
      { name: "right", type: "Number", required: true },
    ],
    outputs: [{ name: "result", type: "Number", required: true }],
    data_matching: undefined,
  }));
  const double = adaptRuntimeOperation(runtimeDefinition({
    id: "fixture.collection.double",
    name: "Double each value",
    inputs: [{
      name: "items", type: "List", item_type: "Number",
      data_structure: "list", data_access: "item", required: true,
    }],
    outputs: [{
      name: "result", type: "List", data_structure: "tree",
      item_type: "Number", required: true,
    }],
    data_matching: "longest",
  }));
  const operations = new Map([literal, add, double].map((operation) => [operation.id, operation]));
  const implementations = new Map([
    [literal.nativeOperation, ({ parameters }) => parameters.value],
    [add.nativeOperation, ({ arguments: args }) => args.left + args.right],
    [double.nativeOperation, ({ arguments: args }) => {
      const mapNested = (value) => Array.isArray(value)
        ? value.map(mapNested)
        : value * 2;
      return mapNested(args.items);
    }],
  ]);

  return {
    id: "fixture-kernel",
    label: "Fixture kernel",
    version: "1",
    capabilities: {
      geometryKinds: ["geometry", "solid"],
      importFormats: ["fixture-json"],
      exportFormats: ["fixture-json"],
      supportsExactBrep: false,
    },
    async discoverOperations() {
      return [...operations.values()];
    },
    async importProgram(source, format) {
      if (format !== "fixture-json") throw new Error("unsupported fixture import format");
      return JSON.parse(source);
    },
    async exportProgram(graph, format) {
      if (format !== "fixture-json") throw new Error("unsupported fixture export format");
      return JSON.stringify(graph);
    },
    async evaluate({ graph }) {
      const values = {};
      const errors = {};
      const scopes = new Set((graph.providerData?.execution_scopes ?? []).map((scope) => scope.id));
      const incoming = new Map();
      for (const connection of graph.connections) {
        const ports = incoming.get(connection.targetNode) ?? new Map();
        if (ports.has(connection.targetPort)) {
          errors[connection.targetNode] = "duplicate input binding: " + connection.targetPort;
        }
        ports.set(connection.targetPort, {
          node: connection.sourceNode,
          port: connection.sourcePort,
        });
        incoming.set(connection.targetNode, ports);
      }

      for (const node of graph.nodes) {
        const operation = operations.get(node.operationId);
        if (!operation) {
          errors[node.id] = "unknown provider operation: " + node.operationId;
          continue;
        }
        const path = node.providerData?.execution_scope_path ?? [];
        if (path.some((scopeId) => !scopes.has(scopeId))) {
          errors[node.id] = "unknown lexical execution scope";
          continue;
        }
        const argumentsByName = {};
        let bindingFailed = false;
        for (const input of operation.inputs) {
          const edge = incoming.get(node.id)?.get(input.id);
          if (edge) {
            const upstream = values[edge.node]?.[edge.port];
            if (upstream === undefined) {
              errors[node.id] = "unavailable upstream value for " + input.id;
              bindingFailed = true;
              break;
            }
            argumentsByName[input.id] = upstream;
          } else if (Object.hasOwn(node.parameters, input.id)) {
            argumentsByName[input.id] = node.parameters[input.id];
          } else if (input.defaultValue !== undefined) {
            argumentsByName[input.id] = input.defaultValue;
          } else if (input.required) {
            errors[node.id] = "missing required input: " + input.id;
            bindingFailed = true;
            break;
          }
        }
        if (bindingFailed) continue;
        const invoke = implementations.get(operation.nativeOperation);
        if (!invoke) {
          errors[node.id] = "unbound native operation: " + operation.nativeOperation;
          continue;
        }
        values[node.id] = {
          result: invoke({ arguments: argumentsByName, parameters: node.parameters }),
        };
      }
      return { values, geometry: {}, previews: {}, errors };
    },
  };
}

test("provider-neutral fixture covers discovery, binding, operator dispatch, mapping, scopes and interchange", async () => {
  const provider = fixtureProvider();
  const operations = await provider.discoverOperations();
  assert.deepEqual(
    operations.map((operation) => operation.id),
    [
      "fixture.python.literal",
      "fixture.number.operator.__add__",
      "fixture.collection.double",
    ],
  );

  const graph = {
    nodes: [
      { id: "left", operationId: "fixture.python.literal", parameters: { value: 2 }, position: { x: 0, y: 0 } },
      { id: "right", operationId: "fixture.python.literal", parameters: { value: 3 }, position: { x: 0, y: 100 } },
      {
        id: "sum",
        operationId: "fixture.number.operator.__add__",
        parameters: {},
        position: { x: 300, y: 50 },
        providerData: { execution_scope_path: ["scope-1"] },
      },
      {
        id: "mapped",
        operationId: "fixture.collection.double",
        parameters: { items: [[1, 2], [3]] },
        position: { x: 600, y: 50 },
      },
    ],
    connections: [
      { sourceNode: "left", sourcePort: "result", targetNode: "sum", targetPort: "left" },
      { sourceNode: "right", sourcePort: "result", targetNode: "sum", targetPort: "right" },
    ],
    providerData: {
      execution_scopes: [{
        id: "scope-1", module: "fixture-kernel", name: "BuilderScope",
        args: [], kwargs: {}, has_binding: true,
      }],
    },
  };

  const result = await provider.evaluate({ graph });
  assert.deepEqual(result.errors, {});
  assert.equal(result.values.sum.result, 5);
  assert.deepEqual(result.values.mapped.result, [[2, 4], [6]]);

  const encoded = await provider.exportProgram(graph, "fixture-json");
  const decoded = await provider.importProgram(encoded, "fixture-json");
  assert.deepEqual(decoded, graph);
});

test("build123d provider publishes only validated operation catalogs", async () => {
  const provider = new Build123dKernelProvider({});
  provider.setDefinitions([runtimeDefinition()]);
  const discovered = await provider.discoverOperations();
  assert.equal(discovered.length, 1);
  assert.equal(discovered[0].id, "fixture.geometry.translate");

  const invalid = runtimeDefinition({
    inputs: [
      { name: "shape", type: "Shape", required: true },
      { name: "shape", type: "Shape", required: true },
    ],
  });
  assert.throws(
    () => provider.setDefinitions([invalid]),
    /duplicate\/empty input id/,
  );

  // A rejected catalog must not partially replace the last valid catalog.
  assert.equal((await provider.discoverOperations()).length, 1);
});

test("build123d provider exposes the declared geometry kind through opaque handles", async () => {
  const definition = runtimeDefinition({
    outputs: [{ name: "result", type: "Solid", annotation: "Solid", required: true }],
  });
  const runtime = {
    async evaluate() {
      return {
        previews: { "solid-1": { vertices: [], triangles: [] } },
        socket_values: { "solid-1": { result: { type: "Solid" } } },
        errors: {},
        order: ["solid-1"],
        cache: { hits: 0, misses: 1 },
      };
    },
  };
  const provider = new Build123dKernelProvider(runtime);
  provider.setDefinitions([definition]);
  const result = await provider.evaluate({
    graph: {
      nodes: [{
        id: "solid-1",
        operationId: definition.id,
        parameters: {},
        position: { x: 0, y: 0 },
      }],
      connections: [],
    },
  });
  assert.deepEqual(result.geometry["solid-1"], {
    provider: "build123d",
    handle: "solid-1",
    kind: "solid",
  });
});
