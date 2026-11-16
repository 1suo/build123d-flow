#!/usr/bin/env python3
"""Run the pinned upstream examples through the production interpreter without CAD."""
from __future__ import annotations

import concurrent.futures
import json
import re
import sys
import traceback
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "public" / "python"
SPEC_PATH = ROOT / "tests" / "e2e" / "upstream-examples.spec.ts"
sys.path.insert(0, str(PYTHON_DIR))

import flow_runtime as runtime


def corpus():
    source = SPEC_PATH.read_text(encoding="utf-8")
    commit_match = re.search(r'const UPSTREAM_COMMIT = "([0-9a-f]{40})";', source)
    examples_match = re.search(r"const EXAMPLES = \[(.*?)\];", source, re.S)
    if not commit_match or not examples_match:
        raise RuntimeError("Cannot read pinned upstream corpus from " + str(SPEC_PATH))
    examples = re.findall(r'"([^"]+\.py)"', examples_match.group(1))
    if not examples or len(examples) != len(set(examples)):
        raise RuntimeError("Upstream corpus is empty or contains duplicate filenames")
    return commit_match.group(1), examples


def fetch_example(commit: str, filename: str):
    url = "https://raw.githubusercontent.com/gumyr/build123d/{}/examples/{}".format(
        commit, filename
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "build123d-flow-translator-acceptance/1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def validate_graph(graph, filename):
    if not isinstance(graph, dict):
        raise ValueError("{}: translator did not return a graph object".format(filename))
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("{}: translated graph has no nodes".format(filename))
    if not isinstance(edges, list):
        raise ValueError("{}: translated graph has no edge list".format(filename))
    node_map = {}
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("{}: graph contains a node without a stable id".format(filename))
        if node_id in node_map:
            raise ValueError("{}: duplicate graph node id {}".format(filename, node_id))
        node_map[node_id] = node
        definition_id = node.get("data", {}).get("definition", {}).get("id")
        if definition_id not in runtime._CATALOG:
            raise ValueError(
                "{}: node {} references unknown definition {}".format(
                    filename, node_id, definition_id
                )
            )
    incoming, outgoing, order = runtime._graph_plan(node_map, edges)
    if len(order) != len(nodes):
        raise ValueError("{}: graph planner did not order every node".format(filename))
    for edge in edges:
        source = node_map[edge["source"]]
        target = node_map[edge["target"]]
        source_outputs = source.get("data", {}).get("definition", {}).get("outputs", [])
        target_inputs = target.get("data", {}).get("definition", {}).get("inputs", [])
        if edge.get("sourceHandle") not in {
            item.get("name") for item in source_outputs
        }:
            raise ValueError(
                "{}: edge references unknown output {}.{}".format(
                    filename, edge["source"], edge.get("sourceHandle")
                )
            )
        if edge.get("targetHandle") not in {
            item.get("name") for item in target_inputs
        }:
            raise ValueError(
                "{}: edge references unknown input {}.{}".format(
                    filename, edge["target"], edge.get("targetHandle")
                )
            )
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "scope_count": len(graph.get("execution_scopes", [])),
        "topological_order": order,
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: test_upstream_translator.py CATALOG.json RESULTS.json"
        )
    catalog_path, results_path = map(Path, sys.argv[1:])
    if runtime.b3d is not None or "build123d" in sys.modules or "OCP" in sys.modules:
        raise SystemExit("Gate 1 must run without importing build123d or OCP")
    runtime.load_catalog_bundle(catalog_path.read_text(encoding="utf-8"))
    commit, filenames = corpus()

    fetched = {}
    failures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_example, commit, filename): filename
            for filename in filenames
        }
        for future in concurrent.futures.as_completed(futures):
            filename = futures[future]
            try:
                fetched[filename] = future.result()
            except Exception as exc:
                failures[filename] = {
                    "stage": "fetch",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }

    results = []
    for filename in filenames:
        if filename in failures:
            results.append({"filename": filename, "passed": False, **failures[filename]})
            continue
        try:
            graph = json.loads(runtime.import_python_code(fetched[filename]))
            metrics = validate_graph(graph, filename)
            results.append({
                "filename": filename,
                "passed": True,
                "metrics": metrics,
                "graph": graph,
            })
            print("PASS {} ({} nodes, {} edges)".format(
                filename, metrics["node_count"], metrics["edge_count"]
            ))
        except Exception as exc:
            results.append({
                "filename": filename,
                "passed": False,
                "stage": "translation",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            print("FAIL {}: {}".format(filename, exc))

    passed = sum(1 for result in results if result["passed"])
    payload = {
        "gate": "interpreter-no-kernel",
        "upstream_commit": commit,
        "example_count": len(filenames),
        "passed": passed,
        "failed": len(filenames) - passed,
        "results": results,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("Gate 1: {}/{} examples translated".format(passed, len(filenames)))
    if runtime.b3d is not None or "build123d" in sys.modules or "OCP" in sys.modules:
        print("FAIL: interpreter imported the CAD kernel")
        return 1
    return 0 if passed == len(filenames) else 1


if __name__ == "__main__":
    raise SystemExit(main())
