import type { NodeDefinition, GraphDocument, EvaluationResult } from "../types";
type WorkerMessage = { type: "status"; message: string } | { type: "ready" } | { type: "result"; id: number; value: unknown } | { type: "error"; id?: number; message: string; stack?: string };
export class PythonRuntime {
  private worker: Worker | null = null;
  private ready = false;
  private nextId = 1;
  private pending = new Map<number, { resolve: (value: any) => void; reject: (error: Error) => void }>();
  private initPromise: Promise<void> | null = null;
  private statusHandler: (message: string) => void = () => {};
  async initialize(onStatus: (message: string) => void = () => {}): Promise<void> {
    if (this.ready) return;
    if (this.initPromise) return this.initPromise;
    this.statusHandler = onStatus;
    this.initPromise = new Promise<void>((resolve, reject) => {
      const worker = new Worker(`${import.meta.env.BASE_URL}python/pyodide.worker.js?v=14`, { type: "module" });
      this.worker = worker;
      worker.onmessage = ({ data }: MessageEvent<WorkerMessage>) => {
        if (data.type === "status") this.statusHandler(data.message);
        else if (data.type === "ready") { this.ready = true; resolve(); }
        else if (data.type === "result") {
          const pending = this.pending.get(data.id);
          if (pending) { this.pending.delete(data.id); pending.resolve(data.value); }
        } else if (data.type === "error") {
          const error = new Error(data.message);
          if (data.stack) error.stack = data.stack;
          if (data.id !== undefined) {
            const pending = this.pending.get(data.id);
            if (pending) { this.pending.delete(data.id); pending.reject(error); }
          } else { reject(error); this.failPending(error); }
        }
      };
      worker.onerror = (event) => { const error = new Error(event.message || "Python worker failed"); reject(error); this.failPending(error); };
      worker.postMessage({ type: "initialize", baseUrl: import.meta.env.BASE_URL });
    }).catch((error) => { this.initPromise = null; this.worker?.terminate(); this.worker = null; throw error; });
    return this.initPromise;
  }
  private failPending(error: Error): void { for (const pending of this.pending.values()) pending.reject(error); this.pending.clear(); }
  private request<T>(method: string, payload?: unknown): Promise<T> {
    if (!this.ready || !this.worker) return Promise.reject(new Error("Python runtime is not ready"));
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.worker!.postMessage({ type: "call", id, method, payload }); });
  }
  get isReady(): boolean { return this.ready; }
  getCatalog(): Promise<NodeDefinition[]> { return this.request<NodeDefinition[]>("getCatalog"); }
  discoverCatalog(): Promise<NodeDefinition[]> { return this.request<NodeDefinition[]>("discoverCatalog"); }
  getDiscoveryReport(): Promise<Record<string, unknown>> { return this.request<Record<string, unknown>>("getDiscoveryReport"); }
  evaluate(graph: GraphDocument): Promise<EvaluationResult> { return this.request<EvaluationResult>("evaluate", graph); }
  exportStep(): Promise<Uint8Array | null> { return this.request<Uint8Array | null>("exportStep"); }
  exportPython(graph: GraphDocument): Promise<string> { return this.request<string>("exportPython", graph); }
  importPython(source: string): Promise<GraphDocument> { return this.request<GraphDocument>("importPython", source); }
  terminate(): void { this.worker?.terminate(); this.worker = null; this.ready = false; this.failPending(new Error("Python worker terminated")); }
}
