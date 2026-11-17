#!/usr/bin/env python3
"""Build a self-contained Perigram plugin ZIP from the Vite public asset tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile


REQUIRED_FILES = (
    "perigram-runtime.js",
    "python/pyodide.worker.js",
    "python/perigram_graph.py",
    "python/perigram_runtime.py",
    "python/flow_runtime.py",
    "python/wheels/manifest.json",
    "pyodide/v314.0.7/full/pyodide.mjs",
    "preview/build123d-preview.js",
)


def package_plugin(dist: Path, listing_path: Path, output: Path) -> Path:
    dist = dist.resolve()
    listing_path = listing_path.resolve()
    output = output.resolve()
    if not dist.is_dir():
        raise FileNotFoundError(f"Vite distribution does not exist: {dist}")
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    declarations = listing.get("declarations") or {}
    runtimes = declarations.get("runtimes") or []
    surfaces = declarations.get("surfaces") or []
    if not any(item.get("entry") == "/perigram-runtime.js" for item in runtimes):
        raise ValueError("Perigram listing must point at /perigram-runtime.js")
    if not any(item.get("module") == "/preview/build123d-preview.js" for item in surfaces):
        raise ValueError("Perigram listing must declare /preview/build123d-preview.js")

    missing = [name for name in REQUIRED_FILES if not (dist / name).is_file()]
    if missing:
        raise FileNotFoundError("Plugin package is missing required assets: " + ", ".join(missing))

    wheel_manifest = json.loads((dist / "python/wheels/manifest.json").read_text(encoding="utf-8"))
    wheel_names = []
    for item in wheel_manifest:
        filename = item.get("filename") if isinstance(item, dict) else None
        if not isinstance(filename, str) or not filename or PurePosixPath(filename).name != filename:
            raise ValueError(f"Invalid wheel filename in manifest: {filename!r}")
        if not (dist / "python/wheels" / filename).is_file():
            raise FileNotFoundError(f"Wheel listed in manifest is missing: {filename}")
        wheel_names.append(filename)

    output.parent.mkdir(parents=True, exist_ok=True)
    include_roots = ("python", "pyodide", "preview")
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(listing_path, "listing.json")
        archive.write(dist / "perigram-runtime.js", "perigram-runtime.js")
        for root_name in include_roots:
            root = dist / root_name
            for file_path in sorted(root.rglob("*")):
                if not file_path.is_file():
                    continue
                relative = file_path.relative_to(dist).as_posix()
                archive.write(file_path, relative)

    with ZipFile(output, "r") as archive:
        names = set(archive.namelist())
        required = {"listing.json", *REQUIRED_FILES}
        missing_entries = sorted(required - names)
        if missing_entries:
            raise RuntimeError("Created plugin archive is incomplete: " + ", ".join(missing_entries))
        for filename in wheel_names:
            if f"python/wheels/{filename}" not in names:
                raise RuntimeError(f"Created plugin archive omitted wheel {filename}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--listing", type=Path, default=Path("perigram-listing.json"))
    parser.add_argument("--output", type=Path, default=Path("build123d-flow-perigram-plugin.zip"))
    args = parser.parse_args()
    output = package_plugin(args.dist, args.listing, args.output)
    print(f"Created {output} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
