#!/usr/bin/env python3
"""Run the pinned upstream examples through the production interpreter without CAD."""
from __future__ import annotations

import concurrent.futures
import json
import sys
import traceback
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "public" / "python"
CORPUS_PATH = ROOT / "tests" / "upstream-examples.json"
sys.path.insert(0, str(PYTHON_DIR))

import flow_runtime as runtime


def corpus():
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    repository = data.get("upstream_repository")
    commit = data.get("commit")
    examples = data.get("examples")
    if (
        not isinstance(repository, str)
        or not isinstance(commit, str)
        or not re_full_commit(commit)
        or not isinstance(examples, list)
        or not examples
        or len(examples) != len(set(examples))
        or len(examples) != data.get("expected_example_count")
    ):
        raise RuntimeError("Invalid pinned upstream corpus: " + str(CORPUS_PATH))
    if any(not isinstance(name, str) or not name.endswith(".py") for name in examples):
        raise RuntimeError("Upstream corpus contains an invalid example filename")
    return repository, commit, examples


def re_full_commit(value):
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def fetch_example(repository: str, commit: str, filename: str):
    url = "https://raw.githubusercontent.com/{}/{}/examples/{}".format(
        repository, commit, filename
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
        definition = node.get("data", {}).get("definition")
        definition_id = definition.get("id") if isinstance(definition, dict) else None
        if (
            not isinstance(definition_id, str)
            or definition_id not in runtime._CATALOG
            and (not isinstance(definition, dict) or definition.get("id") != definition_id)
        ):
            raise ValueError(
                "{}: node {} has no catalog or embedded definition {}".format(
                    filename, node_id, definition_id
                )
            )
        if not isinstance(definition.get("inputs", []), list) or not isinstance(
            definition.get("outputs", []), list
        ):
            raise ValueError(
                "{}: node {} has malformed socket definitions".format(filename, node_id)
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
    repository, commit, filenames = corpus()

    fetched = {}
    failures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_example, repository, commit, filename): filename
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
            graph = runtime.import_python_code(fetched[filename])
            metrics = validate_graph(graph, filename)
            results.append({
                "filename": filename,
                "passed": True,
                "metrics": metrics,
                "source": fetched[filename],
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
                "source": fetched[filename],
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
