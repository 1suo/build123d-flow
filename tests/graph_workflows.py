"""Graph-level smoke tests for the browser build123d runtime.

Run after importing flow_runtime.py in Pyodide:
    exec(open("tests/graph_workflows.py").read())
    run_graph_workflow_tests()
"""
import json
import flow_runtime as runtime


def _definition(name):
    matches = [item for item in runtime._CATALOG.values() if item["name"].lower() == name.lower()]
    if not matches:
        raise AssertionError("Missing discovered API node: " + name)
    # Prefer canonical public module entries over methods with the same name.
    return sorted(matches, key=lambda item: (item["kind"] != "geometry", item["id"]))[0]


def _node(definition, node_id, params=None):
    values = {}
    for socket in definition["inputs"]:
        if "default" in socket:
            values[socket["name"]] = socket["default"]
    values.update(params or {})
    return {
        "id": node_id,
        "type": "api",
        "position": {"x": 0, "y": 0},
        "data": {"definition": definition, "params": values, "visible": True},
    }


def _shape_input(definition):
    sockets = definition["inputs"]
    for socket in sockets:
        if socket["type"] in ("Shape", "Solid", "Compound", "Sketch", "Face", "Wire"):
            return socket["name"]
    raise AssertionError("No shape input on " + definition["id"])


def _run(name, nodes, edges, terminal_id):
    graph = {"nodes": nodes, "edges": edges}
    result = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if result["errors"]:
        raise AssertionError(name + " failed: " + json.dumps(result["errors"]))
    if terminal_id not in result["previews"]:
        raise AssertionError(name + " produced no geometry preview")
    step = runtime.export_step_bytes()
    if not step:
        raise AssertionError(name + " produced no STEP data")
    return {"name": name, "preview": True, "step_bytes": len(step)}


def _edge(source, target, target_handle):
    return {
        "id": source + "-" + target,
        "source": source,
        "target": target,
        "sourceHandle": "result",
        "targetHandle": target_handle,
    }


def run_graph_workflow_tests():
    results = []

    box = _definition("Box")
    results.append(_run("Box", [_node(box, "box", {"length": 20, "width": 15, "height": 10})], [], "box"))

    cylinder = _definition("Cylinder")
    results.append(_run("Cylinder", [_node(cylinder, "cylinder", {"radius": 5, "height": 12})], [], "cylinder"))

    rectangle = _definition("Rectangle")
    extrude = _definition("extrude")
    extrude_input = _shape_input(extrude)
    extrude_nodes = [
        _node(rectangle, "profile", {"width": 20, "height": 12}),
        _node(extrude, "extrude", {"amount": 8}),
    ]
    results.append(_run(
        "Rectangle → extrude",
        extrude_nodes,
        [_edge("profile", "extrude", extrude_input)],
        "extrude",
    ))

    revolve = _definition("revolve")
    revolve_input = _shape_input(revolve)
    revolve_nodes = [
        _node(rectangle, "profile", {"width": 8, "height": 20}),
        _node(revolve, "revolve"),
    ]
    results.append(_run(
        "Rectangle → revolve",
        revolve_nodes,
        [_edge("profile", "revolve", revolve_input)],
        "revolve",
    ))

    union = _definition("Union")
    union_inputs = [socket["name"] for socket in union["inputs"]]
    if len(union_inputs) < 2:
        raise AssertionError("Union does not expose two inputs")
    boolean_nodes = [
        _node(box, "box_a", {"length": 12, "width": 12, "height": 12}),
        _node(cylinder, "cylinder_b", {"radius": 5, "height": 16}),
        _node(union, "union"),
    ]
    results.append(_run(
        "Box + Cylinder → Union",
        boolean_nodes,
        [
            _edge("box_a", "union", union_inputs[0]),
            _edge("cylinder_b", "union", union_inputs[1]),
        ],
        "union",
    ))

    return results
