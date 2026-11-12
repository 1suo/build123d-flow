import type { NodeDefinition, GraphDocument, EvaluationResult } from "../types";

const PYODIDE_VERSION = "314.0.7";
const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENVIRONMENT_MARKER = ".build123d-flow-environment";

declare global {
  interface Window {
    loadPyodide: (options?: Record<string, unknown>) => Promise<any>;
  }
}

function syncFilesystem(FS: any, populate: boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    FS.syncfs(populate, (error: unknown) => error ? reject(error) : resolve());
  });
}

function clearDirectory(FS: any, directory: string): void {
  for (const entry of FS.readdir(directory)) {
    if (entry === "." || entry === "..") continue;
    const path = `${directory}/${entry}`;
    const stat = FS.stat(path);
    if (FS.isDir(stat.mode)) {
      clearDirectory(FS, path);
      FS.rmdir(path);
    } else {
      FS.unlink(path);
    }
  }
}

async function fingerprint(value: string): Promise<string> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    // Keep persistence usable in restricted contexts; bump this fallback when
    // the Python bootstrap changes and SubtleCrypto is unavailable.
    return "setup-revision-1";
  }
}

export class PythonRuntime {
  private pyodide: any = null;
  private ready = false;
  private initPromise: Promise<void> | null = null;
  private persistentStorage = false;
  private restoredEnvironment = false;

  async initialize(onStatus: (message: string) => void = () => {}): Promise<void> {
    if (this.ready) return;
    if (this.initPromise) return this.initPromise;

    this.initPromise = (async () => {
      onStatus("Preparing Python environment…");
      const [setup, runtime] = await Promise.all([
        fetch(`${import.meta.env.BASE_URL}python/setup.py`).then((response) => {
          if (!response.ok) throw new Error(`Could not load Python setup: ${response.status}`);
          return response.text();
        }),
        fetch(`${import.meta.env.BASE_URL}python/flow_runtime.py`).then((response) => {
          if (!response.ok) throw new Error(`Could not load graph runtime: ${response.status}`);
          return response.text();
        }),
      ]);
      const environmentKey = `${PYODIDE_VERSION}:${await fingerprint(setup)}`;

      onStatus("Loading Python engine…");
      if (!window.loadPyodide) {
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement("script");
          script.src = `${PYODIDE_INDEX_URL}pyodide.js`;
          script.async = true;
          script.onload = () => resolve();
          script.onerror = () => reject(new Error("Could not load Pyodide"));
          document.head.appendChild(script);
        });
      }
      if (!window.loadPyodide) throw new Error("Pyodide script did not initialize");

      this.pyodide = await window.loadPyodide({
        indexURL: PYODIDE_INDEX_URL,
        fsInit: async (FS: any, info: { sitePackages: string }) => {
          try {
            if (!FS.filesystems?.IDBFS) throw new Error("IndexedDB filesystem is unavailable");
            FS.mount(FS.filesystems.IDBFS, {}, info.sitePackages);
            this.persistentStorage = true;
            onStatus("Checking saved Python environment…");
            await syncFilesystem(FS, true);

            let savedKey = "";
            try {
              savedKey = new TextDecoder().decode(FS.readFile(`${info.sitePackages}/${ENVIRONMENT_MARKER}`));
            } catch { /* First visit, or an older environment without a marker. */ }

            if (savedKey === environmentKey) {
              this.restoredEnvironment = true;
              onStatus("Restored Python environment from this browser");
            } else {
              clearDirectory(FS, info.sitePackages);
              onStatus("Preparing a fresh Python environment…");
            }
          } catch (error) {
            this.persistentStorage = false;
            onStatus(`Persistent Python cache unavailable; using temporary storage (${String(error)})`);
          }
        },
      });

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

      await this.pyodide.loadPackage(["micropip"]);
      onStatus(this.restoredEnvironment ? "Verifying saved Python packages…" : "Installing Python dependencies…");
      await this.pyodide.runPythonAsync(setup);

      onStatus("Loading graph runtime…");
      this.pyodide.FS.writeFile("/flow_runtime.py", runtime);
      await this.pyodide.runPythonAsync("import sys; sys.path.insert(0, '/')");
      await this.pyodide.runPythonAsync("import flow_runtime");

      if (this.persistentStorage) {
        try {
          onStatus("Saving Python environment for the next visit…");
          const sitePackages = this.pyodide.runPython("import sysconfig; sysconfig.get_paths()['purelib']");
          this.pyodide.FS.writeFile(`${sitePackages}/${ENVIRONMENT_MARKER}`, environmentKey);
          await syncFilesystem(this.pyodide.FS, false);
          onStatus(this.restoredEnvironment ? "Python environment restored" : "Python environment saved");
        } catch (error) {
          onStatus(`Could not save Python environment; this session is still usable (${String(error)})`);
        }
      }

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

  async discoverCatalog(): Promise<NodeDefinition[]> {
    if (!this.ready) throw new Error("Python runtime is not ready");
    const json = await this.pyodide.runPythonAsync("flow_runtime.discover_full()");
    return JSON.parse(json);
  }

  getDiscoveryReport(): Record<string, unknown> {
    if (!this.ready) throw new Error("Python runtime is not ready");
    return JSON.parse(this.pyodide.runPython("flow_runtime.discovery_report_json()"));
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
