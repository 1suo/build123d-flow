import test from "node:test";
import assert from "node:assert/strict";
import { createRuntime } from "../public/perigram-runtime.js";

class FakeWorker {
  constructor() {
    this.messages = [];
    this.terminated = false;
    this.onmessage = null;
    this.onerror = null;
    this.definitions = [
      {
        provider: "build123d",
        id: "build123d.Box",
        execution: { runtime: "build123d-pyodide", targets: ["browser"] },
        preview: { renderer: "cad-mesh" }
      },
      {
        provider: "perigram.python",
        id: "core.python.scope.binding",
        execution: { runtime: "build123d-pyodide", targets: ["browser"] }
      }
    ];
  }

  postMessage(message) {
    this.messages.push(message);
    if (message.type === "initialize") {
      queueMicrotask(() => this.onmessage?.({ data: { type: "ready" } }));
      return;
    }
    if (message.type !== "call") return;
    let value;
    if (message.method === "getProviderDefinitions") value = this.definitions;
    else if (message.method === "executeDiscoveredOperation") {
      value = { result: message.payload.inputs?.width ?? 1 };
      this.lastExecution = message.payload;
    } else value = true;
    queueMicrotask(() => this.onmessage?.({
      data: { type: "result", id: message.id, value }
    }));
  }

  terminate() {
    this.terminated = true;
  }
}

test("Perigram runtime publishes discovered provider contributions", async () => {
  const worker = new FakeWorker();
  const runtime = await createRuntime({ createWorker: () => worker });
  assert.deepEqual(
    runtime.providers.map(item => item.provider).sort(),
    ["build123d", "perigram.python"]
  );
  assert.equal(runtime.providers[0].definitions.length, 1);
  const build123dProvider = runtime.providers.find(item => item.provider === "build123d");
  assert.equal(build123dProvider.previewRenderers["cad-mesh"], "build123d-cad-preview");
  runtime.dispose();
  assert.equal(worker.terminated, true);
});

test("Perigram runtime dispatches invocation inputs and graph execution identity", async () => {
  const worker = new FakeWorker();
  const runtime = await createRuntime({ createWorker: () => worker });
  const result = await runtime.execute("build123d.Box", {
    node: { id: "box-node" },
    config: { graphId: "graph-1", executionScopePath: [] },
    inputs: { width: 12 }
  }, {});

  assert.deepEqual(result, { result: 12 });
  assert.equal(await runtime.clearExecution("graph-1"), true);
  assert.equal(worker.messages.some(item => item.method === "clearExecution"), true);
  assert.equal(worker.lastExecution.operationId, "build123d.Box");
  assert.deepEqual(worker.lastExecution.inputs, { width: 12 });
  assert.equal(worker.lastExecution.config.nodeId, "box-node");
  assert.equal(worker.lastExecution.config.graphId, "graph-1");
  assert.equal(worker.lastExecution.config.executionId, "graph-1");
  runtime.dispose();
});

test("cancellation terminates and the next invocation boots a clean Python worker", async () => {
  const workers = [];
  const runtime = await createRuntime({
    createWorker: () => {
      const worker = new FakeWorker();
      workers.push(worker);
      return worker;
    }
  });
  const controller = new AbortController();
  const cancelled = runtime.execute("build123d.Box", {
    node: { id: "box-cancelled" },
    config: { graphId: "graph-1" },
    inputs: {}
  }, { signal: controller.signal });
  controller.abort();
  await assert.rejects(cancelled, /cancelled/);
  assert.equal(workers[0].terminated, true);
  assert.equal(await runtime.clearExecution("graph-1"), false);
  assert.equal(workers.length, 1);

  const result = await runtime.execute("build123d.Box", {
    node: { id: "box-retried" },
    config: { graphId: "graph-1" },
    inputs: {}
  }, {});
  assert.deepEqual(result, { result: 1 });
  assert.equal(workers.length, 2);
  assert.equal(workers[1].terminated, false);
  runtime.dispose();
});

test("builder scope operations share the graph session when the host omits a run id", async () => {
  const worker = new FakeWorker();
  const runtime = await createRuntime({ createWorker: () => worker });
  const context = {};
  await runtime.execute("core.python.scope.binding", {
    node: { id: "scope-node" },
    config: { graphId: "graph-1", executionScopePath: ["scope-1"] },
    inputs: {},
    context
  }, {});
  await runtime.execute("build123d.Box", {
    node: { id: "box-node" },
    config: { graphId: "graph-1", executionScopePath: ["scope-1"] },
    inputs: { width: 2 },
    context
  }, {});
  const executions = worker.messages.filter(item => item.method === "executeDiscoveredOperation");
  assert.equal(executions.length, 2);
  assert.equal(executions[0].payload.config.executionId, "graph-1");
  assert.equal(executions[1].payload.config.executionId, "graph-1");
  runtime.dispose();
});
