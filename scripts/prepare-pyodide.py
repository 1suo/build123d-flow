#!/usr/bin/env python3
"""Stage Pyodide and only the bundled packages required by setup.py."""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
setup_path = Path(sys.argv[3])
destination.mkdir(parents=True, exist_ok=True)

core_files = {
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def setup_package_roots(path: Path) -> set[str]:
    """Read literal requirements passed to micropip.install; no duplicate list."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = {"micropip"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "install"
            and isinstance(func.value, ast.Name)
            and func.value.id == "micropip"
        ):
            continue
        if any(
            keyword.arg == "deps"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        ):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        values = arg.elts if isinstance(arg, (ast.List, ast.Tuple, ast.Set)) else [arg]
        for value in values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", value.value)
            if match:
                roots.add(normalized(match.group(1)))
    return roots


with tarfile.open(archive, "r:bz2") as bundle:
    members = [member for member in bundle.getmembers() if member.isfile()]
    by_name = {}
    for member in members:
        by_name.setdefault(Path(member.name).name, member)

    missing = core_files - by_name.keys()
    if missing:
        raise SystemExit("Pyodide archive is missing runtime files: " + ", ".join(sorted(missing)))

    lock_stream = bundle.extractfile(by_name["pyodide-lock.json"])
    if lock_stream is None:
        raise SystemExit("Could not read pyodide-lock.json")
    lock = json.load(lock_stream)
    packages = lock.get("packages", {})
    package_names = {normalized(name): name for name in packages}

    roots = setup_package_roots(setup_path)
    required_packages = set()
    pending = sorted(roots)
    while pending:
        requested = pending.pop()
        package_name = package_names.get(requested)
        # Packages supplied only by PyPI (or by the app's explicit mock/wheel)
        # have no Pyodide bundle entry and are intentionally not staged here.
        if package_name is None or package_name in required_packages:
            continue
        required_packages.add(package_name)
        pending.extend(normalized(dep) for dep in packages[package_name].get("depends", []))

    required_files = set(core_files)
    for package_name in required_packages:
        filename = packages[package_name].get("file_name")
        if not filename:
            raise SystemExit("Pyodide lockfile has no wheel filename for " + package_name)
        wheel = Path(filename).name
        required_files.add(wheel)
        required_files.add(wheel + ".metadata")

    missing_wheels = {name for name in required_files if name.endswith(".whl") and name not in by_name}
    if missing_wheels:
        raise SystemExit("Pyodide archive is missing required wheels: " + ", ".join(sorted(missing_wheels)))

    staged_bytes = 0
    for name in sorted(required_files):
        member = by_name.get(name)
        if member is None:
            if name.endswith(".metadata"):
                continue
            raise SystemExit("Pyodide archive is missing required file: " + name)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit("Could not read " + name)
        with source, (destination / name).open("wb") as target:
            shutil.copyfileobj(source, target)
        staged_bytes += member.size

print("Staged Pyodide runtime files:")
for path in sorted(destination.iterdir()):
    print("  " + path.name + " (" + format(path.stat().st_size, ",") + " bytes)")
print("Pyodide package roots from setup.py: " + ", ".join(sorted(roots)))
print("Staged dependency closure: " + ", ".join(sorted(required_packages)))
print("Staged {} files ({:,} bytes total)".format(len(required_files), staged_bytes))
