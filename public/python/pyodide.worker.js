/* Pyodide 314 requires an ES-module worker. Keep the loader same-origin. */
import { loadPyodide } from "../pyodide/v314.0.7/full/pyodide.mjs";
const VERSION="314.0.7";
let pyodide=null;
const status=message=>self.postMessage({type:"status",message});
async function initialize(base){
 status("Preparing Python environment in background worker…");
 const [setup,runtime]=await Promise.all(["setup.py","flow_runtime.py"].map(async f=>{const r=await fetch(base+"python/"+f);if(!r.ok)throw Error("Could not load "+f+": "+r.status);return r.text();}));
 const indexURL=new URL(base+"pyodide/v"+VERSION+"/full/",self.location.href).href;
 status("Loading local Pyodide runtime…");
 pyodide=await loadPyodide({indexURL});
 pyodide.setStdout({batched:s=>{if(s.trim())status(s.trim());}});pyodide.setStderr({batched:s=>{if(s.trim())status("Python: "+s.trim());}});
 status("Loading micropip from the local runtime…");await pyodide.loadPackage(["micropip"]);
 status("Installing Python dependencies…");await pyodide.runPythonAsync(setup);
 status("Loading graph runtime…");pyodide.FS.writeFile("/flow_runtime.py",runtime);await pyodide.runPythonAsync("import sys; sys.path.insert(0, '/')");await pyodide.runPythonAsync("import flow_runtime");
 status("build123d ready");self.postMessage({type:"ready"});
}
async function invoke(method,payload){
 if(method==="getCatalog")return JSON.parse(pyodide.runPython("flow_runtime.catalog_json()"));
 if(method==="discoverCatalog")return JSON.parse(await pyodide.runPythonAsync("flow_runtime.discover_full()"));
 if(method==="getDiscoveryReport")return JSON.parse(pyodide.runPython("flow_runtime.discovery_report_json()"));
 if(method==="evaluate"){const fn=pyodide.runPython("flow_runtime.evaluate_graph_json");try{const r=await fn(JSON.stringify(payload)),s=typeof r==="string"?r:r.toJs();if(typeof r!=="string")r.destroy();return JSON.parse(s);}finally{fn.destroy();}}
 if(method==="exportStep"){const r=pyodide.runPython("flow_runtime.export_step_bytes()");if(r===null)return null;try{return r.toJs();}finally{r.destroy();}}
 if(method==="exportPython"){const fn=pyodide.runPython("flow_runtime.export_python_code");try{const r=await fn(JSON.stringify(payload));if(typeof r==="string")return r;try{return r.toJs();}finally{r.destroy();}}finally{fn.destroy();}}
 throw Error("Unknown runtime method: "+method);
}
self.onmessage=async({data})=>{try{if(data.type==="initialize")await initialize(data.baseUrl);else if(data.type==="call"){const value=await invoke(data.method,data.payload);if(value instanceof Uint8Array)self.postMessage({type:"result",id:data.id,value},[value.buffer]);else self.postMessage({type:"result",id:data.id,value});}}catch(e){self.postMessage({type:"error",id:data.id,message:String(e),stack:e?.stack});}};
