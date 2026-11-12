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


def _shape_operator(method_name):
    shape_class = runtime.b3d.Shape
    node_id = shape_class.__module__ + "." + shape_class.__name__ + ".operator." + method_name
    if node_id not in runtime._CATALOG:
        raise AssertionError("Missing native build123d operator: " + node_id)
    return runtime._CATALOG[node_id]


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



def _core_node(node_id, instance_id, params=None):
    definition = runtime._CATALOG[node_id]
    return _node(definition, instance_id, params)


def run_data_mapping_tests():
    add = lambda values: values["a"] + values["b"]
    longest = runtime._map_item_inputs(
        {"a": [1, 2, 3], "b": [10]}, {"a": "item", "b": "item"}, add, "longest"
    )
    shortest = runtime._map_item_inputs(
        {"a": [1, 2, 3], "b": [10]}, {"a": "item", "b": "item"}, add, "shortest"
    )
    cross = runtime._map_item_inputs(
        {"a": [1, 2], "b": [10, 20]}, {"a": "item", "b": "item"}, add, "cross"
    )
    nested = runtime._map_item_inputs(
        {"a": [[1, 2], [3]], "b": [10, 20]},
        {"a": "item", "b": "item"}, add, "longest"
    )
    if longest != [11, 12, 13] or shortest != [11] or cross != [11, 21, 12, 22]:
        raise AssertionError("List matching modes returned unexpected values")
    if nested != [[11, 12], [23]]:
        raise AssertionError("Nested list mapping did not preserve branch structure")
    shape_list = runtime._annotation_metadata(list[runtime.b3d.Face])
    if shape_list.get("data_structure") != "list" or shape_list.get("item_type") != "Face":
        raise AssertionError("List[Face] metadata conflates collection and element types")
    return {"name": "data mapping", "longest": True, "shortest": True, "cross": True, "nested": True}


def run_collection_workflow_tests():
    nodes = [
        _core_node("core.series.range", "range", {"start": 0, "stop": 5, "step": 1}),
        _core_node("core.list.map", "mapped", {"expression": "x * 2"}),
        _core_node("core.series.range", "sqrt_range", {"start": 1, "stop": 10, "step": 1}),
        _core_node("core.math.sqrt", "sqrt_each"),
        _core_node("core.dict.construct", "dictionary", {"keys": ["a", "b"], "values": [2, 4]}),
        _core_node("core.convert.to_set", "set_a", {"value": [1, 2, 2, 3]}),
        _core_node("core.convert.to_set", "set_b", {"value": [3, 4]}),
        _core_node("core.set.union", "set_union"),
    ]
    edges = [
        _edge("range", "mapped", "items"),
        _edge("sqrt_range", "sqrt_each", "value"),
        _edge("set_a", "set_union", "a"),
        _edge("set_b", "set_union", "b"),
    ]
    result = json.loads(runtime.evaluate_graph_json(json.dumps({"nodes": nodes, "edges": edges})))
    if result["errors"]:
        raise AssertionError("Collection workflows failed: " + json.dumps(result["errors"]))
    mapped = result["socket_values"]["mapped"]["result"]
    if [item["value"] for item in mapped["items"]] != [0, 2, 4, 6, 8]:
        raise AssertionError("List Map returned unexpected values: " + json.dumps(mapped))
    sqrt_values = result["socket_values"]["sqrt_each"]["result"]
    expected_sqrt = [1.0, 1.41421, 1.73205, 2.0, 2.23607, 2.44949, 2.64575, 2.82843, 3.0]
    if [round(item["value"], 5) for item in sqrt_values["items"]] != expected_sqrt:
        raise AssertionError("Scalar node did not map over list input: " + json.dumps(sqrt_values))
    dictionary = result["socket_values"]["dictionary"]["result"]
    if dictionary["items"]["a"]["value"] != 2 or dictionary["items"]["b"]["value"] != 4:
        raise AssertionError("Dictionary construction returned unexpected values")
    union = result["socket_values"]["set_union"]["result"]
    if union["length"] != 4:
        raise AssertionError("Set union returned an unexpected number of unique values")
    return {"name": "collections and series", "map": True, "dict": True, "set_union": True}


def run_shape_collection_workflow_test():
    box = _definition("Box")
    cylinder = _definition("Cylinder")
    union = _shape_operator("__add__")
    append = runtime._CATALOG["core.list.append"]
    union_inputs = [socket["name"] for socket in union["inputs"]]
    nodes = [
        _node(box, "box_a", {"length": 10, "width": 10, "height": 10}),
        _node(box, "box_b", {"length": 8, "width": 8, "height": 8}),
        _node(cylinder, "cylinder", {"radius": 3, "height": 12}),
        _node(append, "list_a"),
        _node(append, "shape_list"),
        _node(union, "union"),
    ]
    edges = [
        _edge("box_a", "list_a", "value"),
        _edge("list_a", "shape_list", "items"),
        _edge("box_b", "shape_list", "value"),
        _edge("box_a", "union", union_inputs[0]),
        _edge("shape_list", "union", union_inputs[1]),
    ]
    result = _run("List[Shape] → item-wise Union", nodes, edges, "union")
    return {"name": "shape collection", "preview": result["preview"], "step_bytes": result["step_bytes"]}



def run_catalog_metadata_tests():
    """Prove catalog ownership and palette placement are declared, never guessed."""
    catalog = runtime._CATALOG
    for node in catalog.values():
        if node.get("source") not in {"build123d", "python", "flow"}:
            raise AssertionError("Missing/invalid provenance: " + node.get("id", "<unknown>"))
        if not node.get("palette_category") or not node.get("palette_section"):
            raise AssertionError("Missing palette metadata: " + node.get("id", "<unknown>"))

    expected_sources = {
        "core.value.number": "flow",
        "core.value.string": "flow",
        "core.value.boolean": "flow",
        "core.python.function": "python",
    }
    for node_id, expected in expected_sources.items():
        actual = catalog[node_id]["source"]
        if actual != expected:
            raise AssertionError(
                node_id + " is registered as " + actual + "; expected " + expected
            )

    box = _definition("Box")
    if box["source"] != "build123d":
        raise AssertionError("Box must originate from build123d API discovery")
    if box["palette_category"] != "Create":
        raise AssertionError("Geometry constructors must use their declared API palette group")

    for method_name in ("__add__", "__sub__", "__and__"):
        definition = _shape_operator(method_name)
        if definition["source"] != "build123d":
            raise AssertionError(method_name + " must retain native build123d provenance")
        if definition.get("operator", {}).get("method") != method_name:
            raise AssertionError(method_name + " is missing its native operator identity")
        if definition["palette_category"] != "Boolean":
            raise AssertionError(method_name + " is missing its Boolean palette placement")
    if any(node_id.startswith("flow.boolean.") for node_id in catalog):
        raise AssertionError("Legacy app-authored Boolean wrappers remain in the catalog")
    return {"name": "catalog metadata", "explicit_provenance": True, "explicit_palette": True}



def run_graph_workflow_tests():
    results = [run_catalog_metadata_tests(), run_collection_workflow_tests(), run_data_mapping_tests(), run_shape_collection_workflow_test()]

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

    union = _shape_operator("__add__")
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

    # Exercise the three native Shape algebra operators, not app adapters.
    for method_name in ("__sub__", "__and__"):
        operation = _shape_operator(method_name)
        operation_inputs = [socket["name"] for socket in operation["inputs"]]
        operation_nodes = [
            _node(box, "base", {"length": 12, "width": 12, "height": 12}),
            _node(cylinder, "tool", {"radius": 5, "height": 16}),
            _node(operation, "operation"),
        ]
        results.append(_run(
            "Box " + operation["operator"]["symbol"] + " Cylinder (native)",
            operation_nodes,
            [
                _edge("base", "operation", operation_inputs[0]),
                _edge("tool", "operation", operation_inputs[1]),
            ],
            "operation",
        ))

    return results
