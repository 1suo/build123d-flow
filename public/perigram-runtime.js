/* Perigram's JavaScript runtime entry. Python and CAD values stay in the
 * nested Pyodide worker; this module is only the JSON message bridge. */

export async function createRuntime({ createWorker } = {}) {
  const workerFactory = createWorker || (() => new Worker(
    new URL("./python/pyodide.worker.js", import.meta.url),
    { type: "module", name: "build123d-pyodide" }
  ));
  let activeState = null;
  let statePromise = null;
  let disposed = false;
  function executionIdFor(context, config) {
    // The current Perigram evaluator supplies one execution id to every node
    // in a preview. Graph id is a stable fallback for older host versions.
    return String(
      context?.executionId || context?.runId ||
      config.executionId || config.runId || config.graphId || "build123d-default"
    );
  }

  async function bootWorker() {
    const worker = workerFactory();
    const pending = new Map();
    let sequence = 0;
    let readyResolve;
    let readyReject;
    const ready = new Promise((resolve, reject) => {
      readyResolve = resolve;
      readyReject = reject;
    });

    const state = {
      worker,
      providers: [],
      call(method, payload = {}) {
        if (disposed) return Promise.reject(new Error("build123d runtime is disposed"));
        const id = "build123d-" + (++sequence);
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject });
          try {
            worker.postMessage({ type: "call", id, method, payload });
          } catch (error) {
            pending.delete(id);
            reject(error);
          }
        });
      },
      terminate(error = new Error("build123d worker was terminated")) {
        if (activeState === state) activeState = null;
        for (const request of pending.values()) request.reject(error);
        pending.clear();
        worker.terminate();
      }
    };

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
      if (activeState === state) activeState = null;
      worker.terminate();
    };

    try {
      worker.postMessage({
        type: "initialize",
        baseUrl: new URL("./", import.meta.url).href
      });
      await ready;
      const definitions = await state.call("getProviderDefinitions");
      if (!Array.isArray(definitions)) {
        throw new Error("build123d provider discovery must return an array");
      }

      const grouped = new Map();
      for (const definition of definitions) {
        if (!definition || typeof definition.provider !== "string" || !definition.id) {
          throw new Error("Discovered build123d node definition is incomplete");
        }
        if (definition.execution?.runtime !== "build123d-pyodide" ||
            !definition.execution?.targets?.includes("browser")) {
          throw new Error("Discovered node " + definition.id +
            " does not target the build123d-pyodide browser runtime");
        }
        if (!grouped.has(definition.provider)) grouped.set(definition.provider, []);
        grouped.get(definition.provider).push(definition);
      }
      state.providers = [...grouped].map(([provider, providerDefinitions]) => ({
        provider,
        definitions: providerDefinitions,
        ...(provider === "build123d"
          ? { previewRenderers: { "cad-mesh": "build123d-cad-preview" } }
          : {})
      }));
      return state;
    } catch (error) {
      state.terminate(error);
      throw error;
    }
  }

  async function ensureWorker() {
    if (disposed) throw new Error("build123d runtime is disposed");
    if (activeState) return activeState;
    if (!statePromise) {
      statePromise = bootWorker().then(state => {
        if (disposed) {
          state.terminate(new Error("build123d runtime was disposed during worker startup"));
          throw new Error("build123d runtime is disposed");
        }
        activeState = state;
        return state;
      }).finally(() => {
        statePromise = null;
      });
    }
    return statePromise;
  }

  const initialState = await ensureWorker();

  return {
    get providers() {
      return initialState.providers;
    },

    async execute(operationId, invocation, { signal } = {}) {
      if (disposed) throw new Error("build123d runtime is disposed");
      if (signal?.aborted) throw new Error("build123d operation was cancelled");
      const state = await ensureWorker();
      const node = invocation?.node || {};
      const config = { ...(invocation?.config || {}) };
      const context = invocation?.context || {};
      config.nodeId = String(node.id || config.nodeId || operationId);
      config.graphId = String(context.graphId || config.graphId || "build123d-graph");
      config.executionId = executionIdFor(context, config);

      return await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (callback, value) => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener("abort", onAbort);
          callback(value);
        };
        const onAbort = () => {
          const error = new Error("build123d operation was cancelled; its Python worker was terminated");
          state.terminate(error);
          finish(reject, error);
        };
        signal?.addEventListener("abort", onAbort, { once: true });
        if (signal?.aborted) {
          onAbort();
          return;
        }
        state.call("executeDiscoveredOperation", {
          operationId,
          inputs: invocation?.inputs || {},
          config
        }).then(
          value => finish(resolve, value),
          error => finish(reject, error)
        );
      });
    },

    async clearExecution(executionId) {
      const state = await ensureWorker();
      return await state.call("clearExecution", { executionId: String(executionId) });
    },

    async clearRuntime() {
      const state = await ensureWorker();
      await state.call("clearRuntime");
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      activeState?.terminate(new Error("build123d runtime was disposed"));
    }
  };
}

export default createRuntime;
