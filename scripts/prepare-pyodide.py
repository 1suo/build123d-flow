#!/usr/bin/env python3
"""Stage only the Pyodide runtime and micropip wheel dependency closure for Pages."""
from __future__ import annotations

import json
import shutil
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.mkdir(parents=True, exist_ok=True)

# Pyodide wheels used by micropip for the build123d/ezdxf/requests stack.
PYODIDE_PACKAGE_ROOTS = {
    "micropip", "requests", "pyparsing", "wrapt", "deprecated", "svgwrite",
    "idna", "packaging", "urllib3", "charset-normalizer", "certifi",
}

core_files = {
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
}

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

    required_packages = set()
    pending = sorted(PYODIDE_PACKAGE_ROOTS)
    while pending:
        package_name = pending.pop()
        if package_name in required_packages:
            continue
        package = packages.get(package_name)
        if package is None:
            raise SystemExit("Pyodide lockfile has no package entry for " + package_name)
        required_packages.add(package_name)
        pending.extend(package.get("depends", []))

    required_files = set(core_files)
    for package_name in required_packages:
        filename = packages[package_name].get("file_name")
        if not filename:
            raise SystemExit("Pyodide lockfile has no wheel filename for " + package_name)
        required_files.add(Path(filename).name)
        required_files.add(Path(filename).name + ".metadata")

    missing_wheels = {name for name in required_files if name.endswith(".whl") and name not in by_name}
    if missing_wheels:
        raise SystemExit("Pyodide archive is missing required wheels: " + ", ".join(sorted(missing_wheels)))

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

print("Staged Pyodide runtime files:")
for path in sorted(destination.iterdir()):
    print("  " + path.name + " (" + format(path.stat().st_size, ",") + " bytes)")
print("Micropip dependency closure: " + ", ".join(sorted(required_packages)))
