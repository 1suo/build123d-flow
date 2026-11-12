#!/usr/bin/env python3
"""Stage the Pyodide lockfile closure of runtime requirements, including PyPI metadata edges."""
from __future__ import annotations
import ast, json, re, shutil, sys, tarfile, urllib.request
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.version import Version, InvalidVersion

CORE = {"pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"}
ENV = {"implementation_name":"cpython", "implementation_version":"3.14.0", "os_name":"posix", "platform_machine":"wasm32", "platform_python_implementation":"CPython", "platform_release":"", "platform_system":"Emscripten", "platform_version":"", "python_full_version":"3.14.0", "python_version":"3.14", "sys_platform":"emscripten", "extra":""}

def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()

def setup_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots, mocks = [Requirement("micropip")], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute): continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id != "micropip": continue
        if node.func.attr == "add_mock_package" and node.args and isinstance(node.args[0], ast.Constant):
            mocks.add(norm(node.args[0].value))
        if node.func.attr != "install" or not node.args: continue
        arg = node.args[0]
        for value in (arg.elts if isinstance(arg, (ast.List, ast.Tuple, ast.Set)) else [arg]):
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str): continue
            raw = value.value.strip()
            if raw.endswith(".whl") or " @ https://" in raw: continue
            roots.append(Requirement(raw))
    return roots, mocks

class Resolver:
    def __init__(self, packages, mocks):
        self.packages = packages
        self.names = {norm(n): n for n in packages}
        self.mocks, self.required, self.visited, self.cache = mocks, set(), set(), {}

    def pypi_requires(self, req):
        key = (norm(req.name), str(req.specifier))
        if key in self.cache: return self.cache[key]
        def get(url):
            request = urllib.request.Request(url, headers={"User-Agent":"build123d-flow-runtime-stager/1"})
            with urllib.request.urlopen(request, timeout=30) as response: return json.load(response)
        project = get(f"https://pypi.org/pypi/{req.name}/json")
        versions = []
        for raw in project.get("releases", {}):
            try: version = Version(raw)
            except InvalidVersion: continue
            if not version.is_prerelease and req.specifier.contains(version): versions.append(version)
        if not versions: raise RuntimeError(f"No PyPI release satisfies {req}")
        version = str(max(versions))
        metadata = get(f"https://pypi.org/pypi/{req.name}/{version}/json")["info"]
        self.cache[key] = metadata.get("requires_dist") or []
        return self.cache[key]

    def add(self, req):
        name = norm(req.name)
        if name in self.mocks: return
        lock_name = self.names.get(name)
        if lock_name:
            package = self.packages[lock_name]
            version = package.get("version")
            if not version or not req.specifier or req.specifier.contains(version, prereleases=True):
                if lock_name in self.required: return
                self.required.add(lock_name)
                for dep in package.get("depends", []): self.add(Requirement(dep))
                return
        key = (name, str(req.specifier))
        if key in self.visited: return
        self.visited.add(key)
        for raw in self.pypi_requires(req):
            dep = Requirement(raw)
            if dep.marker is None or dep.marker.evaluate(ENV): self.add(dep)

def stage(archive, destination, setup):
    roots, mocks = setup_roots(setup)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as bundle:
        members = {}
        for m in bundle.getmembers():
            if m.isfile(): members.setdefault(Path(m.name).name, m)
        missing = CORE - members.keys()
        if missing: raise SystemExit("Missing Pyodide runtime files: " + ", ".join(sorted(missing)))
        lock_file = bundle.extractfile(members["pyodide-lock.json"])
        if lock_file is None: raise SystemExit("Could not read pyodide-lock.json")
        packages = json.load(lock_file).get("packages", {})
        resolver = Resolver(packages, mocks)
        for root in roots: resolver.add(root)
        required = set(CORE)
        for name in resolver.required:
            filename = packages[name].get("file_name")
            if not filename: raise SystemExit(f"No wheel filename in Pyodide lock for {name}")
            wheel = Path(filename).name
            required.update((wheel, wheel + ".metadata"))
        missing = {n for n in required if n.endswith(".whl") and n not in members}
        if missing: raise SystemExit("Missing Pyodide wheels: " + ", ".join(sorted(missing)))
        total = 0
        for name in sorted(required):
            member = members.get(name)
            if member is None:
                if name.endswith(".metadata"): continue
                raise SystemExit("Missing Pyodide archive file: " + name)
            source = bundle.extractfile(member)
            if source is None: raise SystemExit("Cannot read " + name)
            with source, (destination / name).open("wb") as target: shutil.copyfileobj(source, target)
            total += member.size
    print("Runtime roots:", ", ".join(sorted({r.name for r in roots})))
    print("Mocked distributions:", ", ".join(sorted(mocks)))
    print("Staged Pyodide closure:", ", ".join(sorted(resolver.required)))
    print(f"Staged {len(required)} files ({total:,} bytes)")
    return resolver.required

if __name__ == "__main__":
    if len(sys.argv) != 4: raise SystemExit("Usage: prepare-pyodide.py ARCHIVE DESTINATION SETUP_PY")
    stage(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
