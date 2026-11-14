#!/usr/bin/env python3
"""Stage the complete browser Python environment: Pyodide lock wheels and PyPI wheels."""
from __future__ import annotations
import ast, hashlib, json, re, shutil, sys, tarfile, urllib.request
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.version import Version, InvalidVersion

CORE = {"pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"}
ENV = {"implementation_name":"cpython", "implementation_version":"3.14.0", "os_name":"posix", "platform_machine":"wasm32", "platform_python_implementation":"CPython", "platform_release":"", "platform_system":"Emscripten", "platform_version":"", "python_full_version":"3.14.0", "python_version":"3.14", "sys_platform":"emscripten", "extra":""}
WHEEL_TAGS = ("cp314-cp314-pyemscripten_2026_0_wasm32", "py3-none-any")

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
        self.external = {}

    @staticmethod
    def get_json(url):
        request = urllib.request.Request(url, headers={"User-Agent":"build123d-flow-runtime-stager/2"})
        with urllib.request.urlopen(request, timeout=45) as response: return json.load(response)

    def pypi_release(self, req):
        key = (norm(req.name), str(req.specifier))
        if key in self.cache: return self.cache[key]
        project = self.get_json(f"https://pypi.org/pypi/{req.name}/json")
        versions = []
        for raw in project.get("releases", {}):
            try: version = Version(raw)
            except InvalidVersion: continue
            if not version.is_prerelease and req.specifier.contains(version): versions.append(version)
        if not versions: raise RuntimeError(f"No PyPI release satisfies {req}")
        version = str(max(versions))
        release = self.get_json(f"https://pypi.org/pypi/{req.name}/{version}/json")
        info = release["info"]
        candidates = [f for f in release.get("urls", []) if f.get("packagetype") == "bdist_wheel"]
        selected = next((f for tag in WHEEL_TAGS for f in candidates if f["filename"].endswith(tag + ".whl")), None)
        if selected is None:
            found = ", ".join(f["filename"] for f in candidates) or "no wheels"
            raise RuntimeError(f"No compatible Pyodide wheel for {req}=={version}; available: {found}")
        result = (version, info.get("requires_dist") or [], selected)
        self.cache[key] = result
        return result

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
        version, requires, wheel = self.pypi_release(req)
        self.external[name] = {"name": req.name, "version": version, "filename": wheel["filename"],
                               "url": wheel["url"], "sha256": wheel["digests"]["sha256"]}
        for raw in requires:
            dep = Requirement(raw)
            if dep.marker is None or dep.marker.evaluate(ENV): self.add(dep)

def download(url, destination, expected_hash):
    request = urllib.request.Request(url, headers={"User-Agent":"build123d-flow-runtime-stager/2"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as target:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            target.write(chunk)
    actual = digest.hexdigest()
    if actual != expected_hash:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for {destination.name}: {actual} != {expected_hash}")

def stage(archive, destination, setup, wheelhouse):
    roots, mocks = setup_roots(setup)
    destination.mkdir(parents=True, exist_ok=True)
    wheelhouse.mkdir(parents=True, exist_ok=True)
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
    manifest = []
    for item in sorted(resolver.external.values(), key=lambda x: x["name"].lower()):
        target = wheelhouse / item["filename"]
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            download(item["url"], target, item["sha256"])
        total += target.stat().st_size
        manifest.append({k: item[k] for k in ("name", "version", "filename", "sha256")})
    (wheelhouse / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Runtime roots:", ", ".join(sorted({r.name for r in roots})))
    print("Mocked distributions:", ", ".join(sorted(mocks)))
    print("Staged Pyodide closure:", ", ".join(sorted(resolver.required)))
    print("Staged PyPI wheels:", ", ".join(f'{x["name"]}=={x["version"]}' for x in manifest))
    print(f"Staged {len(required)} Pyodide files and {len(manifest)} PyPI wheels ({total:,} bytes)")

if __name__ == "__main__":
    if len(sys.argv) != 5: raise SystemExit("Usage: prepare-pyodide.py ARCHIVE DESTINATION SETUP_PY WHEELHOUSE")
    stage(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
