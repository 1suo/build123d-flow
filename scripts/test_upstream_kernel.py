#!/usr/bin/env python3
"""Execute Gate 1 graphs with the native kernel, isolating every example."""
from __future__ import annotations

import collections
import io
import json
import multiprocessing
import os
import sys
import tarfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "public" / "python"))
EXAMPLE_TIMEOUT_SECONDS = int(os.environ.get("BUILD123D_EXAMPLE_TIMEOUT_SECONDS", "120"))


def definition_ids(graph):
    return collections.Counter(
        node.get("data", {}).get("definition", {}).get("id")
        for node in graph.get("nodes", [])
    )


def prepare_kernel_runtime(catalog_path: Path):
    """Discover native callables once; forked example workers inherit this snapshot."""
    import flow_runtime as runtime

    if runtime.b3d is None:
        raise RuntimeError("Gate 2 requires the real build123d kernel")
    runtime.discover(include_full=True)
    discovered_callables = dict(runtime._CATALOG_CALLABLES)
    discovered_signatures = dict(runtime._CATALOG_SIGNATURES)
    discovered_hints = dict(runtime._CATALOG_HINT_TARGETS)
    runtime.load_catalog_bundle(catalog_path.read_text(encoding="utf-8"))
    # Gate 1 and Gate 2 use identical discovered metadata. Retain the real native
    # callables/hints, but only for definitions present in the serialized catalog.
    runtime._CATALOG_CALLABLES.update({
        key: value for key, value in discovered_callables.items()
        if key in runtime._CATALOG
    })
    runtime._CATALOG_SIGNATURES.update({
        key: value for key, value in discovered_signatures.items()
        if key in runtime._CATALOG
    })
    runtime._CATALOG_HINT_TARGETS.update({
        key: value for key, value in discovered_hints.items()
        if key in runtime._CATALOG
    })
    return runtime


def kernel_worker(item, runtime, result_connection):
    """Evaluate one graph in a disposable fork of the preinitialized kernel."""
    filename = item["filename"]
    try:
        # Register graph-lowering callables in this isolated process, then execute
        # the exact graph produced by the kernel-free Gate 1 interpreter.
        kernel_translation = runtime.import_python_code(item["source"])
        expected_ids = definition_ids(item["graph"])
        actual_ids = definition_ids(kernel_translation)
        if expected_ids != actual_ids:
            raise ValueError(
                "kernel-backed translation differs from Gate 1 graph definitions; "
                "kernel-only={} interpreter-only={}".format(
                    list((actual_ids - expected_ids).elements()),
                    list((expected_ids - actual_ids).elements()),
                )
            )

        evaluation = json.loads(
            runtime.evaluate_graph_json(
                json.dumps(item["graph"]), include_previews=False
            )
        )
        errors = evaluation.get("errors") or {}
        if errors:
            raise ValueError("graph execution errors: {}".format(errors))

        shape = runtime._LAST_SHAPE
        if shape is None:
            raise ValueError("evaluator did not retain a terminal CAD shape")
        vertex_count = len(shape.vertices())
        edge_count = len(shape.edges())
        face_count = len(shape.faces())
        if not (vertex_count or edge_count or face_count):
            raise ValueError("evaluation produced an empty native CAD shape")
        validity = getattr(shape, "is_valid", None)
        if callable(validity):
            validity = validity()
        if validity is False:
            raise ValueError("terminal CAD shape is invalid")
        size = shape.bounding_box().size
        dimensions = [float(size.X), float(size.Y), float(size.Z)]
        if any(value < 0 for value in dimensions):
            raise ValueError("invalid negative bounding-box dimension")

        result = {
            "filename": filename,
            "passed": True,
            "geometry": {
                "preview_count": len(evaluation.get("previews") or {}),
                "native_topology": {
                    "vertices": vertex_count,
                    "edges": edge_count,
                    "faces": face_count,
                },
                "bounds_size": dimensions,
                "valid": validity is not False,
            },
        }
    except Exception as exc:
        result = {
            "filename": filename,
            "passed": False,
            "stage": "kernel-execution",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    try:
        result_connection.send(result)
    finally:
        result_connection.close()


def run_isolated_example(context, item, runtime):
    """Fork a clean evaluator state and retain a hard per-example kill boundary."""
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=kernel_worker,
        args=(item, runtime, child_connection),
    )
    process.start()
    child_connection.close()
    try:
        if not parent_connection.poll(EXAMPLE_TIMEOUT_SECONDS):
            process.terminate()
            process.join()
            return {
                "filename": item["filename"],
                "passed": False,
                "stage": "kernel-timeout",
                "timeout_seconds": EXAMPLE_TIMEOUT_SECONDS,
                "error": "native kernel execution exceeded the per-example timeout; "
                         "worker process was terminated",
            }
        try:
            result = parent_connection.recv()
        except EOFError:
            result = {
                "filename": item["filename"],
                "passed": False,
                "stage": "kernel-process",
                "error": "worker exited without returning a result (exit code {})".format(
                    process.exitcode
                ),
            }
        process.join()
        if process.exitcode != 0 and result.get("passed"):
            return {
                "filename": item["filename"],
                "passed": False,
                "stage": "kernel-process",
                "error": "worker exited with code {}".format(process.exitcode),
            }
        return result
    finally:
        parent_connection.close()
        if process.is_alive():
            process.terminate()
            process.join()


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: test_upstream_kernel.py GATE1_RESULTS.json CATALOG.json RESULTS.json"
        )

    gate1_path, catalog_path, results_path = map(Path, sys.argv[1:])
    gate1 = json.loads(gate1_path.read_text(encoding="utf-8"))
    if gate1.get("failed") != 0 or gate1.get("passed") != gate1.get("example_count"):
        raise SystemExit("Gate 2 cannot start: Gate 1 results are not completely green")

    results = []
    # Native discovery and catalog hydration are expensive and deterministic.
    # Initialize them once, then fork a fresh process for each graph. This keeps
    # the native kernel kill boundary and clean per-example state without repeating
    # full API discovery 67 times.
    runtime = prepare_kernel_runtime(catalog_path)
    context = multiprocessing.get_context("fork")
    with __import__("tempfile").TemporaryDirectory(prefix="build123d-gate2-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        # Upstream examples use relative STL/3MF/DXF/image assets. Download the
        # exact pinned source archive and preserve its examples directory layout.
        import urllib.request
        archive_url = "https://github.com/{}/archive/{}.tar.gz".format(
            gate1["upstream_repository"], gate1["upstream_commit"]
        )
        request = urllib.request.Request(
            archive_url, headers={"User-Agent": "build123d-flow-acceptance/1"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            archive_bytes = response.read()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = [
                member for member in archive.getmembers()
                if member.name.endswith("/examples") or "/examples/" in member.name
            ]
            archive.extractall(temporary_root, members=members, filter="data")
        example_roots = list(temporary_root.glob("*/examples"))
        if len(example_roots) != 1:
            raise RuntimeError(
                "Pinned upstream archive must contain exactly one examples directory"
            )
        os.chdir(example_roots[0])
        for item in gate1["results"]:
            try:
                result = run_isolated_example(context, item, runtime)
            except Exception as exc:
                result = {
                    "filename": item["filename"],
                    "passed": False,
                    "stage": "kernel-harness",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }

            results.append(result)
            if result.get("passed"):
                geometry = result["geometry"]["native_topology"]
                print(
                    "PASS {} ({} vertices, {} edges, {} faces)".format(
                        item["filename"], geometry["vertices"], geometry["edges"], geometry["faces"]
                    ),
                    flush=True,
                )
            else:
                print(
                    "FAIL {} [{}]: {}".format(
                        item["filename"], result.get("stage"), result.get("error")
                    ),
                    flush=True,
                )

    passed = sum(1 for result in results if result["passed"])
    payload = {
        "gate": "native-kernel-execution",
        "upstream_commit": gate1["upstream_commit"],
        "example_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "per_example_timeout_seconds": EXAMPLE_TIMEOUT_SECONDS,
        "results": results,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("Gate 2: {}/{} examples executed".format(passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
