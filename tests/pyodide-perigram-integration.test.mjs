import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const runtimeDirectory = path.join(root, "public/pyodide/v314.0.7/full");
const wheelsDirectory = path.join(root, "public/python/wheels");
const { loadPyodide } = await import(pathToFileURL(path.join(runtimeDirectory, "pyodide.mjs")).href);
const pyodide = await loadPyodide({ indexURL: runtimeDirectory + path.sep });

try {
  await pyodide.loadPackage(["micropip"]);
  const packageNames = JSON.parse(await readFile(path.join(wheelsDirectory, "pyodide-packages.json"), "utf8"));
  await pyodide.loadPackage(packageNames);

  pyodide.FS.mkdirTree("/python/wheels");
  const manifest = JSON.parse(await readFile(path.join(wheelsDirectory, "manifest.json"), "utf8"));
  for (const item of manifest) {
    const bytes = await readFile(path.join(wheelsDirectory, item.filename));
    pyodide.FS.writeFile("/python/wheels/" + item.filename, bytes);
  }
  pyodide.FS.writeFile("/python/wheels/manifest.json", JSON.stringify(manifest));

  const setup = await readFile(path.join(root, "public/python/setup.py"), "utf8");
  pyodide.runPython("import os; os.environ['BUILD123D_FLOW_ENV_RESTORED'] = '0'");
  await pyodide.runPythonAsync(setup);

  const runtimeSource = await readFile(path.join(root, "public/python/perigram_runtime.py"), "utf8");
  const flowRuntime = `
import build123d as b3d
def execute_discovered_operation(operation_id, inputs, config):
    if operation_id == "build123d.Rectangle":
        return {"result": b3d.Rectangle(2, 3)}
    if operation_id == "build123d.extrude":
        return {"result": b3d.extrude(amount=inputs["amount"])}
    raise AssertionError("Unexpected operation: " + operation_id)
`;
  pyodide.FS.writeFile("/flow_runtime.py", flowRuntime);
  pyodide.FS.writeFile("/perigram_runtime.py", runtimeSource);
  await pyodide.runPythonAsync(`
import sys, importlib.util
sys.path.insert(0, "/")
spec = importlib.util.spec_from_file_location("flow_runtime", "/flow_runtime.py")
flow_runtime = importlib.util.module_from_spec(spec)
sys.modules["flow_runtime"] = flow_runtime
spec.loader.exec_module(flow_runtime)
import perigram_runtime
`);

  const resultProxy = await pyodide.runPythonAsync(`
import json
import build123d as b3d
from perigram_runtime import execute_operation_json, _decode

scopes = [
    {
        "id": "outer",
        "module": b3d.BuildPart.__module__,
        "name": "BuildPart",
        "args": [],
        "kwargs": {},
        "has_binding": True,
        "result_attribute": "part",
    },
    {
        "id": "inner",
        "module": b3d.BuildSketch.__module__,
        "name": "BuildSketch",
        "args": [],
        "kwargs": {},
        "has_binding": True,
        "result_attribute": "sketch",
    },
]

def invoke(operation, node, path, **extra):
    config = {
        "graphId": "pyodide-integration",
        "executionId": "pyodide-integration-run",
        "nodeId": node,
        "executionScopePath": path,
        "executionScopes": scopes,
        **extra,
    }
    return json.loads(execute_operation_json(operation, json.dumps(
        {"amount": 4} if operation == "build123d.extrude" else {}
    ), json.dumps(config)))

outer_binding = invoke(
    "core.python.scope.binding", "scope-outer", ["outer"], scopeBinding="outer"
)
inner_binding = invoke(
    "core.python.scope.binding", "scope-inner", ["outer", "inner"], scopeBinding="inner"
)
invoke("build123d.Rectangle", "rectangle", ["outer", "inner"])
inner_result = invoke(
    "core.python.scope.result", "scope-inner-result", ["outer"], scopeResult="inner"
)
invoke("build123d.extrude", "extrude", ["outer"])
outer_result = invoke(
    "core.python.scope.result", "scope-outer-result", [], scopeResult="outer"
)
part = _decode(outer_result["result"], "pyodide-integration-run")
assert abs(part.volume - 24.0) < 1e-6, part.volume
assert len(part.solids()) == 1, len(part.solids())
from perigram_runtime import clear_execution, _SESSIONS, _VALUE_STORE
assert clear_execution("pyodide-integration-run") is True
assert "pyodide-integration-run" not in _SESSIONS
assert not _VALUE_STORE
json.dumps({
    "volume": part.volume,
    "solids": len(part.solids()),
    "opaqueBindings": [
        outer_binding["result"]["__perigram_runtime__"],
        inner_binding["result"]["__perigram_runtime__"],
    ],
    "innerScopeClosed": "result" in inner_result,
})
`);
  const result = typeof resultProxy === "string" ? resultProxy : resultProxy.toJs();
  if (resultProxy !== result && typeof resultProxy?.destroy === "function") resultProxy.destroy();
  const parsed = JSON.parse(result);
  assert.equal(parsed.volume, 24);
  assert.equal(parsed.solids, 1);
  assert.deepEqual(parsed.opaqueBindings, ["value-ref", "value-ref"]);
  console.log("Pyodide/OCP integration passed:", parsed);
} finally {
  try {
    pyodide.runPython("import perigram_runtime; perigram_runtime.clear_runtime()");
  } catch {}
}
