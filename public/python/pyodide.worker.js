/* Pyodide worker. Runtime source is versioned independently from the app shell. */
import { loadPyodide } from "../pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE_VERSION = "314.0.7";
const RUNTIME_REVISION = "2026-11-13-r13";
const ENV_MARKER = ".build123d-flow-environment-v6";

let pyodide = null;
let initialized = false;

const status = (message) => self.postMessage({ type: "status", message });
const syncFs = (FS, populate) => new Promise((resolve, reject) => {
  FS.syncfs(populate, (error) => error ? reject(error) : resolve());
});

function clearDirectory(FS, directory) {
  for (const name of FS.readdir(directory)) {
    if (name === "." || name === "..") continue;
    const path = directory + "/" + name;
    const stat = FS.stat(path);
    if (FS.isDir(stat.mode)) {
      clearDirectory(FS, path);
      FS.rmdir(path);
    } else {
      FS.unlink(path);
    }
  }
}

// IndexedDB implementations impose a per-value/transaction size limit. The
// complete build123d/OCP environment can exceed that limit, so avoid a doomed
// IDBFS sync and run from the in-memory filesystem instead.
const MAX_PERSISTED_ENV_BYTES = 110 * 1024 * 1024;
function directorySize(FS, directory) {
  let total = 0;
  for (const name of FS.readdir(directory)) {
    if (name === "." || name === "..") continue;
    const path = directory + "/" + name;
    const stat = FS.stat(path);
    total += FS.isDir(stat.mode) ? directorySize(FS, path) : stat.size;
  }
  return total;
}

async function fetchText(url, label) {
  const response = await fetch(url, { cache: "reload" });
  if (!response.ok) throw new Error("Could not load " + label + " (" + response.status + "): " + url);
  return response.text();
}

async function fingerprint(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function validateRuntimeSource(source, url) {
  const required = ["import_python_code", "export_python_code", "evaluate_graph_json"];
  const missing = required.filter((name) =>
    !new RegExp("^def\\s+" + name + "\\s*\\(", "m").test(source)
  );
  if (missing.length) {
    throw new Error(
      "Refusing stale/incomplete graph runtime from " + url +
      "; missing top-level definitions: " + missing.join(", ") +
      "; source length: " + source.length +
      "; revision: " + RUNTIME_REVISION
    );
  }
}

async function loadSources(baseUrl) {
  const base = new URL(baseUrl, self.location.href);
  const runtimeUrl = new URL("python/flow_runtime.py?v=" + RUNTIME_REVISION, base);
  const setupUrl = new URL("python/setup.py?v=" + RUNTIME_REVISION, base);
  const [setup, runtime] = await Promise.all([
    fetchText(setupUrl, "Python dependency setup"),
    fetchText(runtimeUrl, "graph runtime")
  ]);
  validateRuntimeSource(runtime, runtimeUrl.href);
  const sourceRevision = await fingerprint(setup + "\\0" + runtime);
  return { setup, runtime, sourceRevision };
}

async function initializePython(baseUrl) {
  status("Fetching versioned Python runtime…");
  // Validate the actual downloaded source before spending time loading Pyodide
  // or installing build123d. This catches stale CDN/service-worker responses.
  const { setup, runtime, sourceRevision } = await loadSources(baseUrl);
  const environmentRevision = RUNTIME_REVISION + ":" + PYODIDE_VERSION + ":" + sourceRevision;
  const indexURL = new URL(
    "pyodide/v" + PYODIDE_VERSION + "/full/",
    new URL(baseUrl, self.location.href)
  ).href;

  let persistent = false;
  let restored = false;

  status("Starting Pyodide…");
  pyodide = await loadPyodide({
    indexURL,
    fsInit: async (FS, info) => {
      try {
        if (!FS.filesystems || !FS.filesystems.IDBFS) throw new Error("IDBFS unavailable");
        FS.mount(FS.filesystems.IDBFS, {}, info.sitePackages);
        persistent = true;
        status("Checking saved Python environment…");
        await syncFs(FS, true);

        let savedRevision = "";
        try {
          savedRevision = new TextDecoder().decode(FS.readFile(info.sitePackages + "/" + ENV_MARKER));
        } catch {}
        if (savedRevision === environmentRevision) {
          restored = true;
        } else {
          clearDirectory(FS, info.sitePackages);
        }
      } catch (error) {
        persistent = false;
        status("Using temporary Python storage: " + error);
      }
    }
  });

  pyodide.setStdout({ batched: (value) => value.trim() && status(value.trim()) });
  pyodide.setStderr({ batched: (value) => value.trim() && status("Python: " + value.trim()) });

  status("Loading micropip…");
  await pyodide.loadPackage(["micropip"]);
  pyodide.runPython(
    "import os; os.environ['BUILD123D_FLOW_ENV_RESTORED'] = '" + (restored ? "1" : "0") + "'"
  );

  status(restored ? "Restoring Python dependencies…" : "Installing Python dependencies…");
  await pyodide.runPythonAsync(setup);

  status("Importing graph runtime…");
  pyodide.FS.writeFile("/flow_runtime.py", runtime);
  await pyodide.runPythonAsync(
    "import sys, importlib.util; " +
    "sys.path.insert(0, '/'); " +
    "spec = importlib.util.spec_from_file_location('flow_runtime', '/flow_runtime.py'); " +
    "module = importlib.util.module_from_spec(spec); " +
    "sys.modules['flow_runtime'] = module; " +
    "spec.loader.exec_module(module); " +
    "globals()['flow_runtime'] = module"
  );

  const validation = JSON.parse(pyodide.runPython(
    "import sys, json; m=sys.modules['flow_runtime']; json.dumps((" +
    "m.__file__, " +
    "callable(getattr(m, 'import_python_code', None)), " +
    "callable(getattr(m, 'export_python_code', None)), " +
    "callable(getattr(m, 'evaluate_graph_json', None))))"
  ));
  if (!validation[1] || !validation[2] || !validation[3]) {
    throw new Error("Loaded graph runtime failed API validation: " + JSON.stringify(validation));
  }

  if (persistent) {
    try {
      const sitePackages = pyodide.runPython("import sysconfig; sysconfig.get_paths()['purelib']");
      const size = directorySize(pyodide.FS, sitePackages);
      if (size > MAX_PERSISTED_ENV_BYTES) {
        persistent = false;
        status(
          "Python environment is " + (size / (1024 * 1024)).toFixed(1) +
          " MB; browser cache limit is 110 MB. Using temporary storage."
        );
      } else {
        pyodide.FS.writeFile(
          sitePackages + "/" + ENV_MARKER,
          environmentRevision
        );
        await syncFs(pyodide.FS, false);
      }
    } catch (error) {
      persistent = false;
      status("Using temporary Python storage: " + error);
    }
  }

  initialized = true;
  status("build123d ready");
  self.postMessage({ type: "ready" });
}

function toJs(value) {
  if (typeof value === "string") return value;
  try {
    return value.toJs({ dict_converter: Object.fromEntries });
  } finally {
    value.destroy();
  }
}

async function invoke(method, payload) {
  if (!initialized || !pyodide) throw new Error("Python runtime is not initialized");

  if (method === "getCatalog") {
    return JSON.parse(pyodide.runPython("flow_runtime.catalog_json()"));
  }
  if (method === "discoverCatalog") {
    return JSON.parse(await pyodide.runPythonAsync("flow_runtime.discover_full()"));
  }
  if (method === "getDiscoveryReport") {
    return JSON.parse(pyodide.runPython("flow_runtime.discovery_report_json()"));
  }
  if (method === "evaluate") {
    const fn = pyodide.runPython("flow_runtime.evaluate_graph_json");
    try {
      const result = await fn(JSON.stringify(payload));
      return typeof result === "string" ? JSON.parse(result) : JSON.parse(toJs(result));
    } finally {
      fn.destroy();
    }
  }
  if (method === "exportStep") {
    const result = pyodide.runPython("flow_runtime.export_step_bytes()");
    if (result === null) return null;
    return toJs(result);
  }
  if (method === "importPython") {
    const fn = pyodide.runPython("flow_runtime.import_python_code");
    try {
      return toJs(fn(payload));
    } finally {
      fn.destroy();
    }
  }
  if (method === "exportPython") {
    const fn = pyodide.runPython("flow_runtime.export_python_code");
    try {
      return toJs(await fn(JSON.stringify(payload)));
    } finally {
      fn.destroy();
    }
  }
  throw new Error("Unknown Python runtime method: " + method);
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "initialize") {
      await initializePython(data.baseUrl);
    } else if (data.type === "call") {
      const value = await invoke(data.method, data.payload);
      if (value instanceof Uint8Array) {
        self.postMessage({ type: "result", id: data.id, value }, [value.buffer]);
      } else {
        self.postMessage({ type: "result", id: data.id, value });
      }
    }
  } catch (error) {
    self.postMessage({
      type: "error",
      id: data.id,
      message: String(error),
      stack: error && error.stack
    });
  }
};
