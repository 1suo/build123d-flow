#!/usr/bin/env python3
"""Execute Gate 1 graphs with the native kernel and a killable persistent worker."""
from __future__ import annotations

import collections
import gc
import io
import json
import os
import select
import subprocess
import sys
import tarfile
import tempfile
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
    """Initialize native discovery once in each disposable worker process."""
    import flow_runtime as runtime

    if runtime.b3d is None:
        raise RuntimeError("Gate 2 requires the real build123d kernel")
    runtime.discover(include_full=True)
    discovered_callables = dict(runtime._CATALOG_CALLABLES)
    discovered_signatures = dict(runtime._CATALOG_SIGNATURES)
    discovered_hints = dict(runtime._CATALOG_HINT_TARGETS)
    runtime.load_catalog_bundle(catalog_path.read_text(encoding="utf-8"))
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


def evaluate_item(item, runtime):
    filename = item["filename"]
    try:
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

        return {
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
        return {
            "filename": filename,
            "passed": False,
            "stage": "kernel-execution",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def worker_server(catalog_path: Path) -> int:
    """Serve one example at a time; parent kills/restarts this process on timeout."""
    import flow_runtime as runtime

    runtime = prepare_kernel_runtime(catalog_path)
    # Graph lowering adds generated Python nodes to these registries. Restore
    # the discovered catalog after every example so examples cannot influence
    # later translation or dispatch.
    baseline_catalog = dict(runtime._CATALOG)
    baseline_callables = dict(runtime._CATALOG_CALLABLES)
    baseline_signatures = dict(runtime._CATALOG_SIGNATURES)
    baseline_hints = dict(runtime._CATALOG_HINT_TARGETS)
    baseline_internal_ids = set(runtime._INTERNAL_NODE_IDS)

    for line in sys.stdin:
        try:
            item = json.loads(line)
            result = evaluate_item(item, runtime)
        except Exception as exc:
            result = {
                "filename": item.get("filename", "<unknown>") if "item" in locals() else "<unknown>",
                "passed": False,
                "stage": "kernel-worker",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        finally:
            runtime._CATALOG.clear()
            runtime._CATALOG.update(baseline_catalog)
            runtime._CATALOG_CALLABLES.clear()
            runtime._CATALOG_CALLABLES.update(baseline_callables)
            runtime._CATALOG_SIGNATURES.clear()
            runtime._CATALOG_SIGNATURES.update(baseline_signatures)
            runtime._CATALOG_HINT_TARGETS.clear()
            runtime._CATALOG_HINT_TARGETS.update(baseline_hints)
            runtime._INTERNAL_NODE_IDS.clear()
            runtime._INTERNAL_NODE_IDS.update(baseline_internal_ids)
            runtime._LAST_SHAPE = None
            gc.collect()
        print(json.dumps(result, allow_nan=False), flush=True)
    return 0


def start_worker(catalog_path: Path, example_root: Path):
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker-server", str(catalog_path.resolve())],
        cwd=str(example_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def run_worker_item(worker, item):
    """Request one graph result with a hard timeout on the worker process."""
    try:
        worker.stdin.write(json.dumps(item, allow_nan=False) + "\n")
        worker.stdin.flush()
        ready, _, _ = select.select([worker.stdout], [], [], EXAMPLE_TIMEOUT_SECONDS)
        if not ready:
            return None, True
        line = worker.stdout.readline()
        if not line:
            return {
                "filename": item["filename"],
                "passed": False,
                "stage": "kernel-process",
                "error": "native worker exited without returning a result",
            }, False
        return json.loads(line), False
    except Exception as exc:
        return {
            "filename": item["filename"],
            "passed": False,
            "stage": "kernel-harness",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }, False


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker-server":
        return worker_server(Path(sys.argv[2]))
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: test_upstream_kernel.py GATE1_RESULTS.json CATALOG.json RESULTS.json"
        )

    gate1_path, catalog_path, results_path = map(Path, sys.argv[1:])
    gate1 = json.loads(gate1_path.read_text(encoding="utf-8"))
    if gate1.get("failed") != 0 or gate1.get("passed") != gate1.get("example_count"):
        raise SystemExit("Gate 2 cannot start: Gate 1 results are not completely green")

    results = []
    with tempfile.TemporaryDirectory(prefix="build123d-gate2-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        # Preserve relative STL/3MF/DXF/image assets from the exact pinned source.
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
        example_root = example_roots[0]

        worker = None
        try:
            for item in gate1["results"]:
                if worker is None or worker.poll() is not None:
                    worker = start_worker(catalog_path, example_root)
                result, timed_out = run_worker_item(worker, item)
                if timed_out:
                    worker.kill()
                    worker.wait()
                    worker = None
                    result = {
                        "filename": item["filename"],
                        "passed": False,
                        "stage": "kernel-timeout",
                        "timeout_seconds": EXAMPLE_TIMEOUT_SECONDS,
                        "error": "native kernel execution exceeded the per-example timeout; "
                                 "worker process was terminated",
                    }
                elif result is None:
                    result = {
                        "filename": item["filename"],
                        "passed": False,
                        "stage": "kernel-harness",
                        "error": "worker returned no result",
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
        finally:
            if worker is not None and worker.poll() is None:
                worker.terminate()
                worker.wait()

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
