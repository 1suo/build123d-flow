import type { NodeDefinition, GraphDocument, EvaluationResult } from "../types";

declare global {
  interface Window {
    loadPyodide: (options?: Record<string, unknown>) => Promise<any>;
  }
}

export class PythonRuntime {
  private pyodide: any = null;
  private ready = false;
  private initPromise: Promise<void> | null = null;

  async initialize(onStatus: (message: string) => void = () => {}): Promise<void> {
    if (this.ready) return;
    if (this.initPromise) return this.initPromise;

    this.initPromise = (async () => {
      onStatus("Loading Pyodide…");
      if (!window.loadPyodide) throw new Error("Pyodide script did not load");
      this.pyodide = await window.loadPyodide();
      await this.pyodide.loadPackage(["micropip"]);

      onStatus("Preparing Python dependencies…");
      this.pyodide.setStdout({
        batched: (output: string) => {
          const message = output.trim();
          if (message) onStatus(message);
        },
      });
      this.pyodide.setStderr({
        batched: (output: string) => {
          const message = output.trim();
          if (message) onStatus(`Python: ${message}`);
        },
      });
      const setup = await fetch("/python/setup.py").then((r) => {
        if (!r.ok) throw new Error(`Could not load Python setup: ${r.status}`);
        return r.text();
      });
      await this.pyodide.runPythonAsync(setup);

      onStatus("Loading graph runtime…");
      const runtime = await fetch("/python/flow_runtime.py").then((r) => {
        if (!r.ok) throw new Error(`Could not load graph runtime: ${r.status}`);
        return r.text();
      });
      this.pyodide.FS.writeFile("/flow_runtime.py", runtime);
      await this.pyodide.runPythonAsync("import sys; sys.path.insert(0, '/')");
      await this.pyodide.runPythonAsync("import flow_runtime");

      this.ready = true;
      onStatus("build123d ready");
    })().catch((error) => {
      this.initPromise = null;
      throw error;
    });

    return this.initPromise;
  }

  get isReady(): boolean {
    return this.ready;
  }

  getCatalog(): NodeDefinition[] {
    if (!this.ready) throw new Error("Python runtime is not ready");
    const json = this.pyodide.runPython("flow_runtime.catalog_json()");
    return JSON.parse(json);
  }

  async evaluate(graph: GraphDocument): Promise<EvaluationResult> {
    if (!this.ready) throw new Error("Python runtime is not ready");
    const payload = JSON.stringify(graph);
    const fn = this.pyodide.runPython("flow_runtime.evaluate_graph_json");
    try {
      const result = await fn(payload);
      const json = typeof result === "string" ? result : result.toJs();
      if (typeof result !== "string") result.destroy();
      return JSON.parse(json);
    } finally {
      fn.destroy();
    }
  }

  exportStep(): Uint8Array | null {
    if (!this.ready) return null;
    const result = this.pyodide.runPython("flow_runtime.export_step_bytes()");
    if (result === null) return null;
    try {
      return result.toJs();
    } finally {
      result.destroy();
    }
  }
}