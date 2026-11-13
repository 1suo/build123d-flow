import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pyodideDir = path.join(root, "public", "pyodide", "v314.0.7", "full");
const { loadPyodide } = await import(pathToFileURL(path.join(pyodideDir, "pyodide.mjs")).href);
const pyodide = await loadPyodide({
  indexURL: pyodideDir,
});

pyodide.setStdout({ batched: (line) => line.trim() && console.log(line.trim()) });
pyodide.setStderr({ batched: (line) => line.trim() && console.error(line.trim()) });

await pyodide.loadPackage(["micropip"]);
const setup = await readFile(path.join(root, "public", "python", "setup.py"), "utf8");
await pyodide.runPythonAsync(setup);

pyodide.FS.writeFile(
  "/flow_runtime.py",
  await readFile(path.join(root, "public", "python", "flow_runtime.py"), "utf8"),
);
pyodide.FS.writeFile(
  "/graph_workflows.py",
  await readFile(path.join(root, "tests", "graph_workflows.py"), "utf8"),
);

await pyodide.runPythonAsync(
  "import sys; sys.path.insert(0, '/'); " +
  "import flow_runtime; import graph_workflows; " +
  "print(graph_workflows.run_python_import_tests()); " +
  "print(graph_workflows.run_python_scope_tests())"
);

console.log("Pyodide Python-to-graph integration tests passed.");
