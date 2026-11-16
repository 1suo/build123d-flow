import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptRuntimeOperation,
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
  assert.deepEqual(scalar.mapping, {
    mode: "scalar",
    preserveNesting: true,
    alignment: "single",
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
