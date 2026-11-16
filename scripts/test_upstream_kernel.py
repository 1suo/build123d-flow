#!/usr/bin/env python3
"""Execute Gate 1's exact graphs with the real build123d kernel, without a browser."""
from __future__ import annotations

import collections
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "public" / "python"))

import flow_runtime as runtime


def definition_ids(graph):
    return collections.Counter(
        node.get("data", {}).get("definition", {}).get("id")
        for node in graph.get("nodes", [])
    )


def validate_geometry(evaluation, filename):
    shape = runtime._LAST_SHAPE
    if shape is None:
        raise ValueError("{}: evaluator did not retain a terminal CAD shape".format(filename))
    vertex_count = len(shape.vertices())
    edge_count = len(shape.edges())
    face_count = len(shape.faces())
    if not (vertex_count or edge_count or face_count):
        raise ValueError("{}: evaluation produced an empty native CAD shape".format(filename))
    validity = getattr(shape, "is_valid", None)
    if callable(validity):
        validity = validity()
    if validity is False:
        raise ValueError("{}: terminal CAD shape is invalid".format(filename))
    bounds = shape.bounding_box()
    size = bounds.size
    dimensions = [float(size.X), float(size.Y), float(size.Z)]
    if any(value < 0 for value in dimensions):
        raise ValueError("{}: invalid negative bounding-box dimension".format(filename))
    return {
        "preview_count": len(evaluation.get("previews") or {}),
        "native_topology": {
            "vertices": vertex_count,
            "edges": edge_count,
            "faces": face_count,
        },
        "bounds_size": dimensions,
        "valid": validity is not False,
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_upstream_kernel.py GATE1_RESULTS.json RESULTS.json")
    gate1_path, results_path = map(Path, sys.argv[1:])
    gate1 = json.loads(gate1_path.read_text(encoding="utf-8"))
    if gate1.get("failed") != 0 or gate1.get("passed") != gate1.get("example_count"):
        raise SystemExit("Gate 2 cannot start: Gate 1 results are not completely green")
    if runtime.b3d is None:
        raise SystemExit("Gate 2 requires the real build123d kernel")

    runtime.discover(include_full=True)
    results = []
    for item in gate1["results"]:
        filename = item["filename"]
        try:
            # Re-run translation only to register the production graph-lowering
            # callables in this kernel process. Evaluation below consumes Gate 1's
            # saved graph, not the graph produced by this registration pass.
            kernel_translation = runtime.import_python_code(item["source"])
            expected_ids = definition_ids(item["graph"])
            actual_ids = definition_ids(kernel_translation)
            if expected_ids != actual_ids:
                raise ValueError(
                    "{}: kernel-backed translation differs from Gate 1 graph definitions; "
                    "kernel-only={} interpreter-only={}".format(
                        filename,
                        list((actual_ids - expected_ids).elements()),
                        list((expected_ids - actual_ids).elements()),
                    )
                )
            evaluation = json.loads(
                runtime.evaluate_graph_json(json.dumps(item["graph"]), include_previews=False)
            )
            errors = evaluation.get("errors") or {}
            if errors:
                raise ValueError("{}: graph execution errors: {}".format(filename, errors))
            geometry = validate_geometry(evaluation, filename)
            results.append({
                "filename": filename,
                "passed": True,
                "geometry": geometry,
            })
            print("PASS {} ({} vertices, {} edges, {} faces)".format(
                filename,
                geometry["native_topology"]["vertices"],
                geometry["native_topology"]["edges"],
                geometry["native_topology"]["faces"],
            ), flush=True)
        except Exception as exc:
            results.append({
                "filename": filename,
                "passed": False,
                "stage": "kernel-execution",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            print("FAIL {}: {}".format(filename, exc), flush=True)

    passed = sum(1 for result in results if result["passed"])
    payload = {
        "gate": "native-kernel-execution",
        "upstream_commit": gate1["upstream_commit"],
        "example_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
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
