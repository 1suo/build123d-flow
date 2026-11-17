/* Perigram's JavaScript runtime entry. Python and CAD values stay in the
 * nested Pyodide worker; this module is only the JSON message bridge. */

export async function createRuntime({ createWorker } = {}) {
  const workerFactory = createWorker || (() => new Worker(
    new URL("./python/pyodide.worker.js", import.meta.url),
    { type: "module", name: "build123d-pyodide" }
  ));
  const worker = workerFactory();
  const pending = new Map();
  let sequence = 0;
  let disposed = false;
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });

  worker.onmessage = ({ data = {} }) => {
    if (data.type === "ready") {
      readyResolve();
      return;
    }
    if (data.type === "status") return;
    const request = pending.get(data.id);
    if (!request) return;
    pending.delete(data.id);
    if (data.type === "error") {
      request.reject(new Error(data.message || "build123d Python runtime failed"));
    } else if (data.type === "result") {
      request.resolve(data.value);
    }
  };
  worker.onerror = event => {
    const error = new Error(event.message || "build123d Python worker failed");
    readyReject(error);
    for (const request of pending.values()) request.reject(error);
    pending.clear();
  };

  function call(method, payload = {}) {
    if (disposed) return Promise.reject(new Error("build123d runtime is disposed"));
    const id = "build123d-" + (++sequence);
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      worker.postMessage({ type: "call", id, method, payload });
    });
  }

  worker.postMessage({
    type: "initialize",
    baseUrl: new URL("./", import.meta.url).href
  });

  await ready;
  const definitions = await call("getProviderDefinitions");
  if (!Array.isArray(definitions)) {
    throw new Error("build123d provider discovery must return an array");
  }

  const grouped = new Map();
  for (const definition of definitions) {
    if (!definition || typeof definition.provider !== "string" || !definition.id) {
      throw new Error("Discovered build123d node definition is incomplete");
    }
    const runtimeId = definition.execution?.runtime;
    if (runtimeId !== "build123d-pyodide" ||
        !definition.execution?.targets?.includes("browser")) {
      throw new Error("Discovered node " + definition.id +
        " does not target the build123d-pyodide browser runtime");
    }
    if (!grouped.has(definition.provider)) {
      grouped.set(definition.provider, []);
    }
    grouped.get(definition.provider).push(definition);
  }

  return {
    providers: [...grouped].map(([provider, providerDefinitions]) => ({
      provider,
      definitions: providerDefinitions
    })),

    async execute(operationId, invocation, { signal } = {}) {
      if (disposed) throw new Error("build123d runtime is disposed");
      if (signal?.aborted) throw new Error("build123d operation was cancelled");
      const node = invocation?.node || {};
      const config = { ...(invocation?.config || {}) };
      const context = invocation?.context || {};
      const scopePath = config.executionScopePath || [];
      const executionId = context.executionId || context.runId ||
        config.executionId || config.runId;
      if (scopePath.length && !executionId) {
        throw new Error(
          "Builder-scoped execution requires a host executionId so Python context " +
          "managers cannot leak across graph runs"
        );
      }
      config.nodeId = String(node.id || config.nodeId || operationId);
      config.graphId = String(context.graphId || config.graphId || "build123d-graph");
      config.executionId = String(executionId || config.graphId);

      const abort = () => {
        // The current Pyodide worker executes synchronously; it cannot be
        // interrupted safely while native WASM code is on its stack.
      };
      signal?.addEventListener("abort", abort, { once: true });
      try {
        if (signal?.aborted) throw new Error("build123d operation was cancelled");
        return await call("executeDiscoveredOperation", {
          operationId,
          inputs: invocation?.inputs || {},
          config
        });
      } finally {
        signal?.removeEventListener("abort", abort);
      }
    },

    async clearRuntime() {
      if (!disposed) await call("clearRuntime");
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      for (const request of pending.values()) {
        request.reject(new Error("build123d runtime was disposed"));
      }
      pending.clear();
      worker.terminate();
    }
  };
}

export default createRuntime;
