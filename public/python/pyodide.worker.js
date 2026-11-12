/* Pyodide 314 requires an ES-module worker. Keep the loader same-origin. */
import { loadPyodide } from "../pyodide/v314.0.7/full/pyodide.mjs";
const VERSION="314.0.7", MARKER=".build123d-flow-environment";
let pyodide=null;
const status=message=>self.postMessage({type:"status",message});
const sync=(FS,populate)=>new Promise((resolve,reject)=>FS.syncfs(populate,e=>e?reject(e):resolve()));
function clear(FS,dir){for(const n of FS.readdir(dir)){if(n==="."||n==="..")continue;const p=dir+"/"+n,s=FS.stat(p);if(FS.isDir(s.mode)){clear(FS,p);FS.rmdir(p);}else FS.unlink(p);}}
async function hash(s){try{const d=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s));return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,"0")).join("");}catch{return "setup-revision-2";}}
async function initialize(base){
 status("Preparing Python environment in background worker…");
 const [setup,runtime]=await Promise.all(["setup.py","flow_runtime.py"].map(async f=>{const r=await fetch(base+"python/"+f);if(!r.ok)throw Error("Could not load "+f+": "+r.status);return r.text();}));
 const key=VERSION+":"+await hash(setup);
 const indexURL=new URL(base+"pyodide/v"+VERSION+"/full/",self.location.href).href;
 status("Loading local Pyodide runtime…");
 let persistent=false,restored=false;
 pyodide=await loadPyodide({indexURL,fsInit:async(FS,info)=>{
  try{if(!FS.filesystems?.IDBFS)throw Error("IDBFS unavailable");FS.mount(FS.filesystems.IDBFS,{},info.sitePackages);persistent=true;status("Checking saved Python environment…");await sync(FS,true);
   let saved="";try{saved=new TextDecoder().decode(FS.readFile(info.sitePackages+"/"+MARKER));}catch{}
   if(saved===key){restored=true;status("Restored Python environment from this browser");}else{clear(FS,info.sitePackages);status("Preparing fresh Python environment…");}
  }catch(e){persistent=false;status("Persistent cache unavailable; using temporary storage ("+e+")");}
 }});
 pyodide.setStdout({batched:s=>{if(s.trim())status(s.trim());}});pyodide.setStderr({batched:s=>{if(s.trim())status("Python: "+s.trim());}});
 status("Loading micropip from the local runtime…");await pyodide.loadPackage(["micropip"]);
 status(restored?"Verifying saved Python packages…":"Installing Python dependencies…");await pyodide.runPythonAsync(setup);
 status("Loading graph runtime…");pyodide.FS.writeFile("/flow_runtime.py",runtime);await pyodide.runPythonAsync("import sys; sys.path.insert(0, '/')");await pyodide.runPythonAsync("import flow_runtime");
 if(persistent)try{status("Saving Python environment…");const dir=pyodide.runPython("import sysconfig; sysconfig.get_paths()['purelib']");pyodide.FS.writeFile(dir+"/"+MARKER,key);await sync(pyodide.FS,false);status(restored?"Python environment restored":"Python environment saved");}catch(e){status("Could not persist environment ("+e+")");}
 status("build123d ready");self.postMessage({type:"ready"});
}
async function invoke(method,payload){
 if(method==="getCatalog")return JSON.parse(pyodide.runPython("flow_runtime.catalog_json()"));
 if(method==="discoverCatalog")return JSON.parse(await pyodide.runPythonAsync("flow_runtime.discover_full()"));
 if(method==="getDiscoveryReport")return JSON.parse(pyodide.runPython("flow_runtime.discovery_report_json()"));
 if(method==="evaluate"){const fn=pyodide.runPython("flow_runtime.evaluate_graph_json");try{const r=await fn(JSON.stringify(payload)),s=typeof r==="string"?r:r.toJs();if(typeof r!=="string")r.destroy();return JSON.parse(s);}finally{fn.destroy();}}
 if(method==="exportStep"){const r=pyodide.runPython("flow_runtime.export_step_bytes()");if(r===null)return null;try{return r.toJs();}finally{r.destroy();}}
 if(method==="exportPython"){const fn=pyodide.runPython("flow_runtime.export_python_code");try{const r=await fn(JSON.stringify(payload));return typeof r==="string"?r:r.toJs();}finally{fn.destroy();}}
 throw Error("Unknown runtime method: "+method);
}
self.onmessage=async({data})=>{try{if(data.type==="initialize")await initialize(data.baseUrl);else if(data.type==="call"){const value=await invoke(data.method,data.payload);if(value instanceof Uint8Array)self.postMessage({type:"result",id:data.id,value},[value.buffer]);else self.postMessage({type:"result",id:data.id,value});}}catch(e){self.postMessage({type:"error",id:data.id,message:String(e),stack:e?.stack});}};
