#!/usr/bin/env python3
"""Stage the self-hosted Pyodide runtime and its complete bundled wheel index."""
from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.mkdir(parents=True, exist_ok=True)

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

    # Keep the complete wheel index available locally. micropip may resolve
    # transitive dependencies to any package bundled by this Pyodide release;
    # staging a hand-picked subset makes those resolutions fail at runtime.
    wheel_names = {
        name for name in by_name
        if name.endswith(".whl") or name.endswith(".whl.metadata")
    }
    required_files = core_files | wheel_names

    staged_bytes = 0
    for name in sorted(required_files):
        member = by_name.get(name)
        if member is None:
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
print("Staged {} bundled wheel/metadata files ({:,} bytes total)".format(
    len(wheel_names), staged_bytes
))
