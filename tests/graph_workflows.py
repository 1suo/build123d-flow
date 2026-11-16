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
        {"a": [1, 2, 3], "b": [10]}, {"a": "list", "b": "list"}, add, "longest"
    )
    shortest = runtime._map_item_inputs(
        {"a": [1, 2, 3], "b": [10]}, {"a": "list", "b": "list"}, add, "shortest"
    )
    cross = runtime._map_item_inputs(
        {"a": [1, 2], "b": [10, 20]}, {"a": "list", "b": "list"}, add, "cross"
    )
    tuple_stream = runtime._map_item_inputs(
        {"a": (1, 2, 3)}, {"a": "tuple"},
        lambda values: values["a"] * 2,
    )
    nested = runtime._map_item_inputs(
        {"a": [[1, 2], [3]], "b": [10, 20]},
        {"a": "tree", "b": "list"}, add, "longest"
    )
    if longest != [11, 12, 13] or shortest != [11] or cross != [11, 21, 12, 22] or tuple_stream != [2, 4, 6]:
        raise AssertionError("List matching modes returned unexpected values")
    if nested != [[11, 12], [23]]:
        raise AssertionError("Nested stream mapping did not preserve tree structure")

    # Runtime container type alone must never trigger mapping.
    vector_value = [1, 2, 3]
    preserved_vector = runtime._map_item_inputs(
        {"vector": vector_value}, {},
        lambda values: values["vector"],
    )
    if preserved_vector is not vector_value:
        raise AssertionError("A list-valued scalar/object parameter was treated as a stream")
    preserved_collection = runtime._map_item_inputs(
        {"items": [1, 2, 3]}, {},
        lambda values: tuple(values["items"]),
    )
    if preserved_collection != (1, 2, 3):
        raise AssertionError("An ordinary collection value was unexpectedly mapped")

    # A stream of list-valued items maps once; only an explicitly tree-shaped
    # stream recursively maps nested list levels.
    list_items = runtime._map_item_inputs(
        {"value": [[1, 2], [3, 4]]}, {"value": "list"},
        lambda values: values["value"],
    )
    tree_items = runtime._map_item_inputs(
        {"value": [[1, 2], [3, 4]]}, {"value": "tree"},
        lambda values: values["value"] * 2,
    )
    if list_items != [[1, 2], [3, 4]] or tree_items != [[2, 4], [6, 8]]:
        raise AssertionError("List and tree stream semantics were conflated")

    shape_list = runtime._annotation_metadata(list[runtime.b3d.Face])
    if shape_list.get("data_structure") != "list" or shape_list.get("item_type") != "Face":
        raise AssertionError("List[Face] metadata conflates collection and element types")
    return {"name": "data mapping", "longest": True, "shortest": True,
            "cross": True, "nested": True, "collections_are_values": True}


def run_annotation_resolution_tests():
    """Cross-module ForwardRefs and bare collection ABCs must stay structural."""
    import typing
    import collections.abc
    union = _shape_operator("__add__")
    other = next(socket for socket in union["inputs"] if socket["name"] != "self")
    if other.get("data_access") != "structure" or other.get("item_type") != "Shape":
        raise AssertionError(
            "Shape + lost its Shape | Iterable[Shape] contract: " + json.dumps(other)
        )
    if "ForwardRef" in other.get("annotation", ""):
        raise AssertionError("Shape + input annotation was not resolved: " + other["annotation"])
    output = union["outputs"][0]
    if not {"Shape", "Compound"}.issubset(set(output.get("variants", []))):
        raise AssertionError("Shape + return union was not resolved: " + json.dumps(output))

    # get_type_hints fails on Shape.__add__'s 'Self | Compound' return because
    # Compound lives in topology.composite, not shape_core. The runtime must
    # still resolve it through a merged namespace.
    for overload in typing.get_overloads(runtime.b3d.Shape.__add__):
        if "Compound" in str(overload.__annotations__.get("return", "")):
            if not runtime._resolve_hints(overload):
                raise AssertionError("Cross-module ForwardRef hints were not resolved")
            break

    # A union of one geometry object and an iterable of geometry objects
    # must preserve an existing collection. Trying Edge(list) first silently
    # wraps the collection as one Edge and fails later inside the kernel.
    edge = runtime.b3d.Edge.make_line((0, 0, 0), (1, 0, 0))
    if runtime._coerce_annotation(edge, runtime.b3d.Edge) is not edge:
        raise AssertionError("Coercing an existing geometry object must preserve its identity")
    edge_union = runtime.b3d.Edge | collections.abc.Iterable[runtime.b3d.Edge]
    coerced_edges = runtime._coerce_annotation([edge], edge_union)
    if not isinstance(coerced_edges, list) or coerced_edges != [edge]:
        raise AssertionError(
            "Geometry-or-iterable union must preserve the iterable: "
            + repr(coerced_edges)
        )

    # A bare Iterable ABC (Pos's v parameter) is a structural contract as well.
    bare_iterable = runtime._annotation_metadata(collections.abc.Iterable)
    if bare_iterable.get("data_structure") != "list" or bare_iterable.get("data_access") is not None:
        raise AssertionError("Bare Iterable lost its collection structure: " + json.dumps(bare_iterable))
    pos = runtime._CATALOG.get("build123d.geometry.Pos.overload-2")
    if pos is not None:
        socket = next(item for item in pos["inputs"] if item["name"] == "v")
        if socket.get("data_access") != "structure":
            raise AssertionError("Pos(v: Iterable) input must consume the collection whole")
        range_node = _core_node("core.series.range", "rng", {"start": 0, "stop": 3, "step": 1})
        pos_node = _node(pos, "pos")
        result = json.loads(runtime.evaluate_graph_json(json.dumps({
            "nodes": [range_node, pos_node], "edges": [_edge("rng", "pos", "v")],
        })))
        if result["errors"]:
            raise AssertionError("Pos(v=list) failed: " + json.dumps(result["errors"]))

    # A numeric stream reaching an item-access object input must be rejected
    # with the socket name, never item-mapped into the native kernel.
    box = _definition("Box")
    moved = runtime._CATALOG["build123d.topology.shape_core.Shape.moved"]
    nodes = [
        _node(box, "box", {"length": 10, "width": 10, "height": 10}),
        _core_node("core.series.range", "rng", {"start": 0, "stop": 3, "step": 1}),
        _node(moved, "moved"),
    ]
    result = json.loads(runtime.evaluate_graph_json(json.dumps({
        "nodes": nodes,
        "edges": [_edge("box", "moved", "self"), _edge("rng", "moved", "loc")],
    })))
    error = result["errors"].get("moved", "")
    if "input 'loc' expects" not in error or "Data mapping at [" in error:
        raise AssertionError("Scalar stream into moved(loc) was not rejected cleanly: " + error)

    # Legitimate Location streams must keep mapping item-wise.
    function = runtime._CATALOG["core.python.function"]
    nodes = [
        _node(box, "box", {"length": 10, "width": 10, "height": 10}),
        _node(function, "locs", {"code": "result = [b3d.Location((x, 0, 0)) for x in range(3)]"}),
        _node(moved, "moved"),
    ]
    result = json.loads(runtime.evaluate_graph_json(json.dumps({
        "nodes": nodes,
        "edges": [_edge("box", "moved", "self"), _edge("locs", "moved", "loc")],
    })))
    if result["errors"]:
        raise AssertionError("Location stream mapping failed: " + json.dumps(result["errors"]))
    return {"name": "annotation resolution", "forward_refs": True, "bare_collections": True,
            "stream_validation": True}


def run_dataflow_shape_inference_tests():
    def node(node_id, inputs, outputs):
        return {"id": node_id, "data": {"definition": {
            "inputs": inputs, "outputs": outputs
        }}}
    nodes = {
        "series": node("series", [], [{"name": "result", "data_structure": "list"}]),
        "mapped": node("mapped", [{"name": "value", "data_access": "item"}],
                       [{"name": "result"}]),
        "terminal": node("terminal", [{"name": "value", "data_access": "structure"}],
                         [{"name": "result"}]),
    }
    incoming = {
        "mapped": {"value": ("series", "result")},
        "terminal": {"value": ("mapped", "result")},
    }
    memo = {}
    if runtime._effective_output_structure("mapped", "result", nodes, incoming, memo) != "list":
        raise AssertionError("Item-wise node must propagate upstream stream shape")
    if runtime._effective_output_structure("terminal", "result", nodes, incoming, memo) != "item":
        raise AssertionError("Structure-consuming node must terminate stream propagation")
    return {"name": "dataflow shape inference", "item_propagation": True,
            "structure_boundary": True}

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
    other_socket = next(socket for socket in union["inputs"] if socket["name"] == union_inputs[1])
    if other_socket.get("data_access") != "structure":
        raise AssertionError("Shape | Iterable[Shape] operator input must preserve the collection")
    result = _run("Shape + Iterable[Shape]", nodes, edges, "union")
    return {"name": "shape collection", "preview": result["preview"], "step_bytes": result["step_bytes"]}



def run_catalog_metadata_tests():
    """Prove catalog ownership and palette placement are declared, never guessed."""
    catalog = runtime._CATALOG
    for node in catalog.values():
        if node.get("source") not in {"build123d", "python", "flow"}:
            raise AssertionError("Missing/invalid provenance: " + node.get("id", "<unknown>"))
        if not node.get("palette_category") or not node.get("palette_section"):
            raise AssertionError("Missing palette metadata: " + node.get("id", "<unknown>"))
        execution = node.get("execution")
        if not isinstance(execution, dict) or not isinstance(execution.get("cacheable"), bool):
            raise AssertionError("Missing explicit execution/cache metadata: " + node.get("id", "<unknown>"))
        if execution["cacheable"] and execution.get("purity") != "pure":
            raise AssertionError("A cacheable node must explicitly declare purity: " + node.get("id", "<unknown>"))

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
        if definition.get("operator", {}).get("family") not in {"arithmetic", "bitwise"}:
            raise AssertionError(method_name + " is missing generic Python operator metadata")
        expected_symbol = {"__add__": "+", "__sub__": "−", "__and__": "&"}[method_name]
        if definition["operator"].get("symbol") != expected_symbol:
            raise AssertionError(method_name + " has an incorrect Python operator symbol")
        native_method = getattr(runtime.b3d.Shape, method_name)
        if runtime._CATALOG_CALLABLES.get(definition["id"]) is not native_method:
            raise AssertionError(method_name + " is not backed by the native Shape method")
        if definition["palette_category"] != "Boolean":
            raise AssertionError(method_name + " is missing its Boolean palette placement")
        if method_name == "__add__":
            other = next(socket for socket in definition["inputs"] if socket["name"] == "other")
            if other.get("item_type") != "Shape" or other.get("data_access") != "structure":
                raise AssertionError("Shape + must retain its Shape | Iterable[Shape] input contract")
            output = definition["outputs"][0]
            if output["type"] != "Shape":
                raise AssertionError("Shape + must expose the common Shape output type")
            if not {"Shape", "Compound"}.issubset(set(output.get("variants", []))):
                raise AssertionError("Shape + must expose its Self | Compound return contract")
    if any(node_id.startswith("flow.boolean.") for node_id in catalog):
        raise AssertionError("Legacy app-authored Boolean wrappers remain in the catalog")
    return {"name": "catalog metadata", "explicit_provenance": True, "explicit_palette": True}



def run_preview_style_test():
    box = _definition("Box")
    style = runtime._CATALOG["core.preview.style"]
    nodes = [
        _node(box, "box", {"length": 10, "width": 12, "height": 14}),
        _node(style, "styled", {"face_color": "#d8c3a5", "edge_color": "#2b2118",
                                "vertex_color": "#ff8800", "opacity": 0.7,
                                "show_edges": True, "show_vertices": False}),
    ]
    edges = [_edge("box", "styled", "shape")]
    result = json.loads(runtime.evaluate_graph_json(json.dumps({"nodes": nodes, "edges": edges})))
    if result["errors"] or "styled" not in result["previews"]:
        raise AssertionError("Preview Style failed: " + json.dumps(result["errors"]))
    if "box" in result["previews"]:
        raise AssertionError("Preview Style must replace, not overlap, the upstream preview")
    preview = result["previews"]["styled"]
    if preview.get("faceColor") != "#d8c3a5" or preview.get("edgeColor") != "#2b2118":
        raise AssertionError("Preview Style colors were not applied")
    if preview.get("opacity") != 0.7 or preview.get("showVertices") is not False:
        raise AssertionError("Preview Style opacity/visibility was not applied")
    if not preview.get("edges") or not preview.get("points"):
        raise AssertionError("Topology edge/vertex geometry was not extracted")
    source = runtime.export_python_code(json.dumps({"nodes": nodes, "edges": edges}))
    compile(source, "<preview-style-export>", "exec")
    if "result = _node_2" not in source:
        raise AssertionError("Preview Style must export as a geometry pass-through")
    return {"name": "preview style and topology", "faces": True, "edges": True, "vertices": True}


def run_python_loop_lowering_tests():
    """Python loops compile once and map their body over iteration sequences."""
    accumulated_sketch = runtime.import_python_code(
        "from math import pi, sin\n"
        "from build123d import Sketch, Plane, Circle\n"
        "slice_count = 10\n"
        "art = Sketch()\n"
        "for i in range(slice_count + 1):\n"
        "    plane = Plane(origin=(0, 0, i * 3), z_dir=(0, 0, 1))\n"
        "    art += plane * Circle(10 * sin(i * pi / slice_count) + 5)\n"
        "result = len(art.faces())\n"
    )
    accumulated_counts = [
        node for node in accumulated_sketch["nodes"]
        if node["data"]["definition"]["id"] == "core.python.builtin.len"
    ]
    if len(accumulated_counts) != 1:
        raise AssertionError("Loop-carried Sketch accumulation must compile to one length operation")
    accumulated_result = json.loads(runtime.evaluate_graph_json(json.dumps(accumulated_sketch)))
    if accumulated_result["errors"]:
        raise AssertionError("Loop-carried augmented assignment failed: " + json.dumps(accumulated_result["errors"]))
    accumulated_count = accumulated_result["socket_values"][accumulated_counts[0]["id"]]["result"]
    if accumulated_count.get("value") != 11:
        raise AssertionError(
            "Augmented assignment must carry the updated Sketch through each loop iteration: "
            + repr(accumulated_count)
        )

    source = (
        "from build123d import Box\n"
        "for length in [1, 2, 3]:\n"
        "    box = Box(length, 2, 3)\n"
        "result = box\n"
    )
    graph = runtime.import_python_code(source)
    box_nodes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    if len(box_nodes) != 1:
        raise AssertionError("A loop body must compile to one Box node, not an unrolled copy per item")
    box_node = box_nodes[0]
    if len(box_node["data"].get("python_loop_sources", [])) != 1:
        raise AssertionError("The single body node is missing its Python loop-scope metadata")
    result = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if result["errors"]:
        raise AssertionError("Mapped loop did not evaluate: " + json.dumps(result["errors"]))
    mapped_boxes = result["socket_values"][box_node["id"]]["result"]
    if mapped_boxes.get("length") != 3:
        raise AssertionError("Loop body operation did not execute once per sequence item: " + repr(mapped_boxes))
    terminal_shapes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.loop_last"
        and node["data"]["definition"]["outputs"][0]["type"] in {"Shape", "Solid", "Compound"}
    ]
    if not terminal_shapes:
        raise AssertionError("Post-loop assignment did not preserve the final geometry type")
    if not any(node["id"] in result["previews"] for node in terminal_shapes):
        raise AssertionError("Post-loop variable did not resolve to the final mapped geometry")

    empty_loop = runtime.import_python_code(
        "from build123d import Box\n"
        "box = Box(2, 2, 2)\n"
        "for length in []:\n"
        "    box = Box(length, 2, 3)\n"
        "result = box\n"
    )
    empty_result = json.loads(runtime.evaluate_graph_json(json.dumps(empty_loop)))
    if empty_result["errors"]:
        raise AssertionError("A zero-iteration loop must preserve the previous binding: " + json.dumps(empty_result["errors"]))

    nested = runtime.import_python_code(
        "from build123d import Box\n"
        "for length in [1, 2]:\n"
        "    for width in [3, 4]:\n"
        "        box = Box(length, width, 1)\n"
    )
    nested_boxes = [
        node for node in nested["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    if len(nested_boxes) != 1:
        raise AssertionError("Nested loops must not duplicate the body operation")
    if len(nested_boxes[0]["data"].get("python_loop_sources", [])) != 2:
        raise AssertionError("Nested loop scopes must both be preserved on the one body node")
    if nested_boxes[0]["data"].get("data_matching") != "cross":
        raise AssertionError("Independent nested loops must use cross-product mapping")
    nested_result = json.loads(runtime.evaluate_graph_json(json.dumps(nested)))
    if nested_result["errors"]:
        raise AssertionError("Independent nested loop mapping failed: " + json.dumps(nested_result["errors"]))
    nested_boxes_value = nested_result["socket_values"][nested_boxes[0]["id"]]["result"]
    nested_rows = nested_boxes_value.get("items", [])
    if (
        nested_boxes_value.get("length") != 2
        or any(row.get("type") != "list" or row.get("length") != 2 for row in nested_rows)
    ):
        raise AssertionError(
            "Independent nested loops must preserve a 2-by-2 Cartesian result tree: "
            + repr(nested_boxes_value)
        )

    repeated = runtime.import_python_code(
        "from build123d import Box\n"
        "for length in [1, 2]:\n"
        "    for width in [3, 4]:\n"
        "        box = Box(1, 1, 1)\n"
    )
    repeated_boxes = [
        node for node in repeated["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    if len(repeated_boxes) != 1:
        raise AssertionError("A loop body that ignores its targets must still compile only once")
    repeated_result = json.loads(runtime.evaluate_graph_json(json.dumps(repeated)))
    if repeated_result["errors"]:
        raise AssertionError("Synthetic loop dependencies failed: " + json.dumps(repeated_result["errors"]))
    repeated_value = repeated_result["socket_values"][repeated_boxes[0]["id"]]["result"]
    repeated_rows = repeated_value.get("items", [])
    if (
        repeated_value.get("length") != 2
        or any(row.get("type") != "list" or row.get("length") != 2 for row in repeated_rows)
    ):
        raise AssertionError(
            "A body independent of loop variables must retain the 2-by-2 nested result tree: "
            + repr(repeated_value)
        )

    face_plane_mapping = runtime.import_python_code(
        "from build123d import Box, Plane\n"
        "shape = Box(2, 2, 2)\n"
        "planes = Plane(shape.faces())\n"
    )
    plane_nodes = [
        node for node in face_plane_mapping["nodes"]
        if node["data"]["definition"]["name"] == "Python Plane"
    ]
    if len(plane_nodes) != 1:
        raise AssertionError("A face collection must connect to one Plane node, not duplicated operations")
    plane_result = json.loads(runtime.evaluate_graph_json(json.dumps(face_plane_mapping)))
    if plane_result["errors"]:
        raise AssertionError("One-node Plane mapping over Face collection failed: " + json.dumps(plane_result["errors"]))
    plane_values = plane_result["socket_values"][plane_nodes[0]["id"]]["result"]
    if plane_values.get("length") != 6:
        raise AssertionError("One Plane node must execute once per face and preserve six results: " + repr(plane_values))

    loop_locations = runtime.import_python_code(
        "from build123d import BuildPart, Box, Locations\n"
        "with BuildPart() as part:\n"
        "    for x in [0, 5, 10]:\n"
        "        with Locations((x, 0, 0)):\n"
        "            Box(1, 1, 1)\n"
    )
    loop_location_boxes = [
        node for node in loop_locations["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    if len(loop_location_boxes) != 1:
        raise AssertionError("A Box inside mapped Locations must remain one node")
    loop_location_result = json.loads(runtime.evaluate_graph_json(json.dumps(loop_locations)))
    if loop_location_result["errors"]:
        raise AssertionError("Tuple-valued Locations inside a loop failed: " + json.dumps(loop_location_result["errors"]))
    part_result_node = next(
        node for node in loop_locations["nodes"]
        if node["data"].get("scope_result")
    )
    part_preview = loop_location_result["previews"].get(part_result_node["id"])
    if not part_preview or not part_preview.get("vertices"):
        raise AssertionError("The mapped Locations builder scope produced no preview geometry")
    x_coordinates = part_preview["vertices"][0::3]
    if max(x_coordinates) - min(x_coordinates) < 10.5:
        raise AssertionError("Locations must preserve all three loop-derived positions: " + repr((min(x_coordinates), max(x_coordinates))))

    shape_list_property = runtime.import_python_code(
        "from build123d import Axis, Box\n"
        "shape = Box(2, 2, 2)\n"
        "last_face = shape.faces().sort_by(Axis.Z).last\n"
    )
    collection_attributes = [
        node for node in shape_list_property["nodes"]
        if node["data"]["definition"]["id"] == "core.python.attribute.structure"
    ]
    if len(collection_attributes) != 1:
        raise AssertionError("ShapeList properties must use a structure-consuming attribute node")
    property_result = json.loads(runtime.evaluate_graph_json(json.dumps(shape_list_property)))
    if property_result["errors"]:
        raise AssertionError("ShapeList.last must run on the collection, not each Face: " + json.dumps(property_result["errors"]))
    if property_result["socket_values"][collection_attributes[0]["id"]]["result"].get("type") != "Face":
        raise AssertionError("ShapeList.last must return one Face")

    numeric_loop = runtime.import_python_code(
        "from build123d import Box\n"
        "for width in [3, 4]:\n"
        "    box = Box(width + 1, 2, 3)\n"
    )
    numeric_boxes = [
        node for node in numeric_loop["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    numeric_result = json.loads(runtime.evaluate_graph_json(json.dumps(numeric_loop)))
    if numeric_result["errors"]:
        raise AssertionError("Numeric operations on typed loop items failed: " + json.dumps(numeric_result["errors"]))
    numeric_value = numeric_result["socket_values"][numeric_boxes[0]["id"]]["result"]
    if numeric_value.get("length") != 2:
        raise AssertionError("Scalar loop arithmetic must map over each item: " + repr(numeric_value))

    dependent_loops = runtime.import_python_code(
        "from build123d import Box\n"
        "shape = Box(2, 2, 2)\n"
        "for face in shape.faces():\n"
        "    for edge in face.edges():\n"
        "        box = Box(1, 1, 1)\n"
    )
    dependent_boxes = [
        node for node in dependent_loops["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
        and len(node["data"].get("python_loop_sources", [])) == 2
    ]
    if len(dependent_boxes) != 1:
        raise AssertionError("Dependent nested loops must retain both scopes on one body node: " + repr([
            (node["id"], node["data"].get("python_loop_sources"))
            for node in dependent_loops["nodes"]
            if node["data"]["definition"]["id"].endswith(".Box")
        ]))
    dependent_result = json.loads(runtime.evaluate_graph_json(json.dumps(dependent_loops)))
    if dependent_result["errors"]:
        raise AssertionError("Dependent nested loops failed: " + json.dumps(dependent_result["errors"]))
    dependent_value = dependent_result["socket_values"][dependent_boxes[0]["id"]]["result"]
    if dependent_value.get("length") != 6 or any(
        item.get("type") != "list" or item.get("length") != 4
        for item in dependent_value.get("items", [])
    ):
        raise AssertionError("Dependent nested loops must preserve the face/edge tree: " + repr(dependent_value))

    mixed_loops = (
        "from build123d import Box\n"
        "shape = Box(2, 2, 2)\n"
        "for face in shape.faces():\n"
        "    for width in [3, 4]:\n"
        "        for edge in face.edges():\n"
        "            box = Box(1, 1, 1)\n"
    )
    try:
        runtime.import_python_code(mixed_loops)
    except ValueError as exc:
        if "Mixed independent and dependent nested loops" not in str(exc):
            raise
    else:
        raise AssertionError("Mixed nested mapping modes must be rejected rather than evaluated incorrectly")

    nested_post_loop = (
        "from build123d import Box\n"
        "for length in [1, 2]:\n"
        "    for width in [3, 4]:\n"
        "        box = Box(length, width, 1)\n"
        "    last_inner_box = box\n"
        "result = last_inner_box\n"
    )
    nested_post_graph = runtime.import_python_code(nested_post_loop)
    nested_post_boxes = [
        node for node in nested_post_graph["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    nested_post_last = [
        node for node in nested_post_graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.loop_last"
    ]
    if len(nested_post_boxes) != 1 or len(nested_post_last) < 2:
        raise AssertionError("Nested post-loop bindings must compose loop_last nodes without unrolling")
    nested_post_result = json.loads(runtime.evaluate_graph_json(json.dumps(nested_post_graph)))
    if nested_post_result["errors"]:
        raise AssertionError("Nested post-loop grouping failed: " + json.dumps(nested_post_result["errors"]))
    inner_group = nested_post_result["socket_values"][nested_post_last[0]["id"]]["result"]
    if inner_group.get("type") != "list" or inner_group.get("length") != 2:
        raise AssertionError("The inner loop's last binding must retain one result per outer iteration: " + repr(inner_group))
    final_nested_box = nested_post_result["socket_values"][nested_post_last[-1]["id"]]["result"]
    if final_nested_box.get("type") not in {"Box", "Solid", "Compound"}:
        debug = [
            {
                "id": node.get("id"),
                "params": node.get("data", {}).get("params"),
                "python_loop_sources": node.get("data", {}).get("python_loop_sources"),
                "links": [
                    {
                        "source": edge.get("source"),
                        "sourceHandle": edge.get("sourceHandle"),
                        "targetHandle": edge.get("targetHandle"),
                    }
                    for edge in nested_post_graph.get("edges", [])
                    if edge.get("target") == node.get("id")
                ],
            }
            for node in nested_post_last
        ]
        raise AssertionError(
            "The outer loop must retain the last inner-loop result: "
            + repr(final_nested_box)
            + "; loop_last nodes: " + repr(debug)
        )

    nested_inner_binding = runtime.import_python_code(
        "from build123d import Box\n"
        "for length in [1, 2]:\n"
        "    for width in [3, 4]:\n"
        "        box = Box(length, width, 1)\n"
        "    result = box\n"
    )
    inner_last_nodes = [
        node for node in nested_inner_binding["nodes"]
        if node["data"]["definition"]["id"] == "core.python.loop_last"
    ]
    inner_binding_result = json.loads(runtime.evaluate_graph_json(json.dumps(nested_inner_binding)))
    if inner_binding_result["errors"]:
        raise AssertionError("An inner post-loop binding inside the outer loop failed: " + json.dumps(inner_binding_result["errors"]))
    if not inner_last_nodes:
        raise AssertionError("An inner loop's last value must be available inside its enclosing loop")

    unpacked = runtime.import_python_code(
        "from build123d import Box\n"
        "for length, width in [(1, 2), (3, 4)]:\n"
        "    box = Box(length, width, 1)\n"
    )
    unpack_subscripts = [
        node for node in unpacked["nodes"]
        if node["data"]["definition"]["id"] == "core.python.subscript"
    ]
    if len(unpack_subscripts) != 2 or any(
        next(socket for socket in node["data"]["definition"]["inputs"] if socket["name"] == "value").get("data_access") != "item"
        for node in unpack_subscripts
    ):
        raise AssertionError("Tuple-unpack subscripts must map over loop tuple items")
    unpacked_result = json.loads(runtime.evaluate_graph_json(json.dumps(unpacked)))
    if unpacked_result["errors"]:
        raise AssertionError("Tuple-unpacking loop target failed: " + json.dumps(unpacked_result["errors"]))

    tuple_comprehension = runtime.import_python_code(
        "values = [tuple(value) for value in [[1, 2], [3, 4]]]\n"
    )
    tuple_nodes = [
        node for node in tuple_comprehension["nodes"]
        if node["data"]["definition"]["id"] == "core.python.builtin.tuple"
    ]
    if len(tuple_nodes) != 1:
        raise AssertionError("tuple() in a comprehension must compile to one mapped node")
    tuple_result = json.loads(runtime.evaluate_graph_json(json.dumps(tuple_comprehension)))
    if tuple_result["errors"]:
        raise AssertionError("Mapped tuple() conversion failed: " + json.dumps(tuple_result["errors"]))
    tuple_value = tuple_result["socket_values"][tuple_nodes[0]["id"]]["result"]
    if tuple_value.get("length") != 2:
        raise AssertionError("tuple() must execute once for each comprehension item")

    comprehension = runtime.import_python_code(
        "from build123d import Box\n"
        "boxes = [Box(1, 2, 3) for length in [1, 2, 3]]\n"
    )
    comprehension_boxes = [
        node for node in comprehension["nodes"]
        if node["data"]["definition"]["id"].endswith(".Box")
    ]
    if len(comprehension_boxes) != 1:
        raise AssertionError("A list comprehension must compile its operation once")
    comprehension_result = json.loads(runtime.evaluate_graph_json(json.dumps(comprehension)))
    if comprehension_result["errors"]:
        raise AssertionError("List-comprehension mapping failed: " + json.dumps(comprehension_result["errors"]))

    constants = runtime.import_python_code("values = [7 for item in [1, 2, 3]]\n")
    constant_result = json.loads(runtime.evaluate_graph_json(json.dumps(constants)))
    if constant_result["errors"]:
        raise AssertionError("Literal list-comprehension mapping failed: " + json.dumps(constant_result["errors"]))

    try:
        runtime.export_python_code(graph)
    except ValueError as exc:
        if "mapped loop scopes" not in str(exc):
            raise
    else:
        raise AssertionError("Python export silently discarded mapped loop semantics")
    return {
        "name": "Python loop lowering",
        "single_body_node": True,
        "nested_cross_product": True,
        "synthetic_loop_dependencies": True,
        "scalar_loop_arithmetic": True,
        "dependent_nested_loops": True,
        "mixed_mapping_rejected": True,
        "nested_post_loop_grouping": True,
        "tuple_unpacking": True,
        "list_comprehension": True,
        "literal_comprehension": True,
        "post_loop_binding": True,
        "lossy_export_rejected": True,
    }


def run_python_value_constructor_tests():
    """All declared build123d object socket types share generic constructor import."""
    cases = [
        ("Vector3", "Vector", "Vector(1, 2, 3)", [1, 2, 3]),
        ("Vector2", "Vector2", "Vector2(1, 2)", [1, 2]),
        ("Rotation", "Rotation", "Rotation(0, 0, 0)", [0, 0, 0]),
        ("Plane", "Plane", "Plane(origin=(0, 0, 0), x_dir=(1, 0, 0), z_dir=(0, 0, 1))",
         {"origin": [0, 0, 0], "x_dir": [1, 0, 0], "z_dir": [0, 0, 1]}),
        ("Location", "Location", "Location((0, 0, 0))", [[0, 0, 0]]),
        ("Axis", "Axis", "Axis((0, 0, 0), (0, 0, 1))", [[0, 0, 0], [0, 0, 1]]),
        ("Color", "Color", "Color(1, 0, 0)", [1, 0, 0]),
        ("Matrix", "Matrix", "Matrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])",
         [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]),
    ]
    results = []
    for index, (socket_type, class_name, constructor, expected) in enumerate(cases):
        if not hasattr(runtime.b3d, class_name):
            continue
        definition_id = "tests.value_constructor." + socket_type.lower()
        definition = {
            "id": definition_id, "name": "ValueSink" + str(index), "kind": "test",
            "inputs": [{"name": "value", "type": socket_type, "required": True}],
            "outputs": [{"name": "result", "type": "Any"}],
        }
        previous = runtime._CATALOG.get(definition_id)
        runtime._CATALOG[definition_id] = definition
        try:
            source = (
                "from build123d import " + class_name + " as ValueCtor\n"
                + "ValueSink" + str(index) + "(value=" + constructor + ")\n"
            )
            graph = runtime.import_python_code(source)
            params = graph["nodes"][0]["data"]["params"]
            if params.get("value") != expected:
                raise AssertionError(
                    socket_type + " constructor was not preserved: " + repr(params.get("value"))
                )
            results.append(socket_type)
        finally:
            if previous is None:
                runtime._CATALOG.pop(definition_id, None)
            else:
                runtime._CATALOG[definition_id] = previous
    if not results:
        raise AssertionError("No build123d value constructors were exercised")

    # A value constructor embedded in a native operator must retain its
    # runtime type. Flattening Location to a coordinate list makes Shape's
    # __rmul__ receive a list instead of a Location.
    operator_graph = runtime.import_python_code(
        "from build123d import Box, Location\n"
        "box = Box(1, 1, 1)\n"
        "moved = Location((0, 0, 0)) * box\n"
    )
    multiply_nodes = [
        node for node in operator_graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.binary.__mul__"
    ]
    if len(multiply_nodes) != 1:
        raise AssertionError("Location * Box must lower to one native operator node")
    def contains_location_tag(value):
        if isinstance(value, dict):
            return value.get("__flow_object__") == "Location" or any(
                contains_location_tag(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_location_tag(item) for item in value)
        return False
    if not contains_location_tag(multiply_nodes[0]["data"].get("params", {})):
        raise AssertionError(
            "Location literal lost its native type before operator dispatch: "
            + repr(multiply_nodes[0]["data"].get("params", {}))
        )
    operator_result = json.loads(runtime.evaluate_graph_json(json.dumps(operator_graph)))
    if operator_result["errors"]:
        raise AssertionError(
            "Native Location * Box failed: " + json.dumps(operator_result["errors"])
        )
    moved_value = operator_result["socket_values"][multiply_nodes[0]["id"]]["result"]
    if moved_value.get("length") != 1:
        raise AssertionError(
            "Location * Box must preserve its native one-item result list: " + repr(moved_value)
        )
    return {
        "name": "Python value constructors",
        "types": results,
        "native_operator_value": True,
    }


def run_python_import_tests():
    """Exercise source parsing, graph construction, and actual graph execution."""
    viewer_examples = [
        "from build123d import Box\nfrom ocp_vscode import show\nbox = Box(10, 10, 10)\nshow(box)\n",
        "import build123d as b3d\nimport ocp_vscode as viewer\nbox = b3d.Box(10, 10, 10)\nviewer.show(box)\n",
        "from build123d import Box\nfrom ocp_vscode import *\nbox = Box(10, 10, 10)\nshow(box)\n",
    ]
    for source in viewer_examples:
        graph = runtime.import_python_code(source)
        if len(graph.get("nodes", [])) != 1 or graph.get("edges"):
            raise AssertionError("ocp_vscode display code changed the geometry graph: " + json.dumps(graph))

    function_graph = runtime.import_python_code(
        "from build123d import Box\n"
        "def make_box(length, width=2):\n"
        "    shape = Box(length, width, 3)\n"
        "    return shape\n"
        "result = make_box(4, width=5)\n"
    )
    function_result = json.loads(runtime.evaluate_graph_json(json.dumps(function_graph)))
    if function_result["errors"]:
        raise AssertionError("A local Python function did not lower to native graph operations: " + json.dumps(function_result["errors"]))
    if function_graph["nodes"][-1]["id"] not in function_result["previews"]:
        raise AssertionError("A returned geometry value from a local function was lost")

    random_graph = runtime.import_python_code(
        "import random\n"
        "random.seed(42)\n"
        "values = [random.randint(1, 10) for _ in range(3)]\n"
    )
    random_result = json.loads(runtime.evaluate_graph_json(json.dumps(random_graph)))
    if random_result["errors"]:
        raise AssertionError("Seeded random graph execution failed: " + json.dumps(random_result["errors"]))
    random_node = next(
        node for node in random_graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.random.batch_item"
    )
    random_values = random_result["socket_values"][random_node["id"]]["result"]
    if [item["value"] for item in random_values["items"]] != [2, 1, 5]:
        raise AssertionError("Seeded random draws did not preserve Python iteration order: " + repr(random_values))

    reduce_graph = runtime.import_python_code(
        "import functools\n"
        "import operator\n"
        "from build123d import Box, Part\n"
        "boxes = [Box(2, 2, 2) for _ in range(2)]\n"
        "result = functools.reduce(operator.add, boxes, Part())\n"
    )
    reduce_result = json.loads(runtime.evaluate_graph_json(json.dumps(reduce_graph)))
    if reduce_result["errors"]:
        raise AssertionError("Graph-backed functools.reduce failed: " + json.dumps(reduce_result["errors"]))
    reduce_node = next(
        node for node in reduce_graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.reduce"
    )
    if reduce_node["id"] not in reduce_result["previews"]:
        raise AssertionError("Graph-backed functools.reduce produced no geometry result")

    sequence_graph = runtime.import_python_code(
        "from build123d import Box\n"
        "left = Box(2, 2, 2).edges()\n"
        "right = Box(3, 3, 3).edges()\n"
        "combined = left + right\n"
        "count = len(combined)\n"
    )
    sequence_result = json.loads(runtime.evaluate_graph_json(json.dumps(sequence_graph)))
    if sequence_result["errors"]:
        raise AssertionError("Python sequence concatenation failed: " + json.dumps(sequence_result["errors"]))
    length_node = next(
        node for node in sequence_graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.builtin.len"
    )
    if sequence_result["socket_values"][length_node["id"]]["result"]["value"] != 24:
        raise AssertionError("ShapeList concatenation did not preserve both source collections")

    assertion_graph = runtime.import_python_code(
        "from build123d import Box\n"
        "shape = Box(2, 2, 2)\n"
        "assert abs(shape.volume - 8.0) < 1e-6, 'volume mismatch'\n"
    )
    assertion_result = json.loads(runtime.evaluate_graph_json(json.dumps(assertion_graph)))
    if assertion_result["errors"]:
        raise AssertionError("Graph-dependent Python assertion failed: " + json.dumps(assertion_result["errors"]))
    if not any(
        node["data"]["definition"]["id"] == "core.python.assert"
        for node in assertion_graph["nodes"]
    ):
        raise AssertionError("Graph-dependent assert was not represented in the procedural graph")

    math_graph = runtime.import_python_code(
        "from math import pi, sin\n"
        "from build123d import Box\n"
        "for index in [0, 1]:\n"
        "    shape = Box(sin(index * pi / 2) + 2, 2, 2)\n"
    )
    math_result = json.loads(runtime.evaluate_graph_json(json.dumps(math_graph)))
    if math_result["errors"]:
        raise AssertionError("Dynamic standard-library math calls failed: " + json.dumps(math_result["errors"]))
    if not any(
        node["data"]["definition"]["id"] == "core.python.stdlib.math.sin"
        for node in math_graph["nodes"]
    ):
        raise AssertionError("Dynamic math.sin was not represented as a graph operation")

    random_order_graph = runtime.import_python_code(
        "import random\n"
        "random.seed(42)\n"
        "values = [random.randint(1, 10) + random.randint(1, 10) for _ in range(2)]\n"
    )
    random_order_result = json.loads(runtime.evaluate_graph_json(json.dumps(random_order_graph)))
    if random_order_result["errors"]:
        raise AssertionError("Multiple random draws in one iteration failed: " + json.dumps(random_order_result["errors"]))
    result_node = random_order_graph["nodes"][-1]
    result_values = random_order_result["socket_values"][result_node["id"]]["result"]
    if [item["value"] for item in result_values["items"]] != [3, 9]:
        raise AssertionError("Random draws were not interleaved in Python evaluation order: " + repr(result_values))

    conditional_graph = runtime.import_python_code(
        "from build123d import Box\n"
        "for index in [0, 1, 2]:\n"
        "    if index == 0:\n"
        "        Box(1, 1, 1)\n"
        "    elif index == 1:\n"
        "        Box(2, 2, 2)\n"
        "    else:\n"
        "        Box(3, 3, 3)\n"
    )
    conditional_result = json.loads(runtime.evaluate_graph_json(json.dumps(conditional_graph)))
    if conditional_result["errors"]:
        raise AssertionError("Conditional branch lowering failed: " + json.dumps(conditional_result["errors"]))
    conditional_nodes = [
        node for node in conditional_graph["nodes"]
        if node["data"].get("python_conditions")
        and node["data"]["definition"].get("name") == "Box"
    ]
    if len(conditional_nodes) != 3:
        raise AssertionError("Expected one condition-gated node per branch, got {}".format(len(conditional_nodes)))
    for node in conditional_nodes:
        result_value = conditional_result["socket_values"][node["id"]]["result"]
        if not isinstance(result_value, dict) or len([
            item for item in result_value.get("items", []) if item is not None
        ]) != 1:
            raise AssertionError("Conditional branch executed for the wrong loop items: " + repr(result_value))

    class_dispatch_graph = runtime.import_python_code(
        "from build123d import Box, Part\n"
        "class SmallPart(Part):\n"
        "    def __init__(self):\n"
        "        shape = Box(1, 1, 1)\n"
        "        super().__init__(shape.wrapped)\n"
        "class LargePart(Part):\n"
        "    def __init__(self):\n"
        "        shape = Box(2, 2, 2)\n"
        "        super().__init__(shape.wrapped)\n"
        "class PartFactory(Part):\n"
        "    makers = {'small': SmallPart, 'large': LargePart}\n"
        "    def __init__(self, kind):\n"
        "        shape = PartFactory.makers[kind]()\n"
        "        super().__init__(shape.wrapped)\n"
        "result = PartFactory('large')\n"
    )
    class_dispatch_result = json.loads(runtime.evaluate_graph_json(json.dumps(class_dispatch_graph)))
    if class_dispatch_result["errors"]:
        raise AssertionError("Class-attribute constructor dispatch failed: " + json.dumps(class_dispatch_result["errors"]))

    # This is the important round-trip boundary: source -> native graph -> evaluator.
    source = (
        "from build123d import Box, Cylinder\n"
        "box = Box(10, 10, 10)\n"
        "cylinder = Cylinder(3, 12)\n"
        "result = box + cylinder\n"
    )
    graph = runtime.import_python_code(source)
    definitions = [node["data"]["definition"]["id"] for node in graph["nodes"]]
    # The native dunder owner is determined by the concrete constructor's
    # Python MRO. Box currently inherits Compound's implementation, so requiring
    # Shape.__add__ here would test a guessed owner rather than Python dispatch.
    import importlib
    constructor_module, constructor_name = definitions[0].rsplit(".", 1)
    constructor_class = getattr(importlib.import_module(constructor_module), constructor_name)
    operator = next(
        (
            owner.__module__ + "." + owner.__name__ + ".operator.__add__"
            for owner in constructor_class.__mro__
            if owner.__module__ + "." + owner.__name__ + ".operator.__add__" in runtime._CATALOG
        ),
        None,
    )
    if len(graph["nodes"]) != 3 or not operator or definitions[-1] != operator:
        raise AssertionError("Python source did not become two constructors and the native MRO union: " + repr(definitions))
    if len(graph["edges"]) != 2:
        raise AssertionError("Python variable/operator dependencies were not wired: " + repr(graph["edges"]))
    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"]:
        raise AssertionError("Imported Python graph did not execute: " + json.dumps(evaluated["errors"]))
    if not any(node["id"] in evaluated["previews"] for node in graph["nodes"][-1:]):
        raise AssertionError("Imported Python graph produced no result preview")

    # Inherited Shape members must resolve for concrete Solid outputs.
    member_graph = runtime.import_python_code(
        "from build123d import Box\n"
        "box = Box(10, 10, 10)\n"
        "faces = box.faces()\n"
        "volume = box.volume\n"
    )
    member_ids = [node["data"]["definition"]["id"] for node in member_graph["nodes"]]
    has_faces_method = any(item.endswith(".Shape.faces") for item in member_ids)
    has_volume_property = any(".volume" in item for item in member_ids) or any(
        item == "core.python.attribute" for item in member_ids[1:]
    )
    if len(member_ids) != 3 or not has_faces_method or not has_volume_property:
        raise AssertionError("Inherited method/property calls were not translated: " + repr(member_ids))
    if len(member_graph["edges"]) != 2:
        raise AssertionError("Inherited member receivers were not connected: " + repr(member_graph["edges"]))
    member_result = json.loads(runtime.evaluate_graph_json(json.dumps(member_graph)))
    if member_result["errors"]:
        raise AssertionError("Imported inherited members did not execute: " + json.dumps(member_result["errors"]))

    # An Any-typed receiver can expose a property name shared by unrelated
    # build123d classes. The importer must defer that lookup to the real object.
    import types
    ambiguous_id = "tests.python.ambiguous_property_receiver"
    previous_definition = runtime._CATALOG.get(ambiguous_id)
    previous_callable = runtime._CATALOG_CALLABLES.get(ambiguous_id)
    runtime._CATALOG[ambiguous_id] = {
        "id": ambiguous_id, "name": "AmbiguousPropertyReceiver",
        "source": "build123d", "kind": "function",
        "inputs": [], "outputs": [{"name": "result", "type": "Any"}],
    }
    runtime._CATALOG_CALLABLES[ambiguous_id] = lambda: types.SimpleNamespace(Y=42)
    try:
        ambiguous_graph = runtime.import_python_code(
            "value = AmbiguousPropertyReceiver().Y\n"
        )
        attribute_nodes = [
            node for node in ambiguous_graph["nodes"]
            if node["data"]["definition"]["id"] == "core.python.attribute"
        ]
        if len(attribute_nodes) != 1:
            raise AssertionError(
                "Ambiguous property did not become a generic Python attribute node: "
                + repr([node["data"]["definition"]["id"] for node in ambiguous_graph["nodes"]])
            )
        ambiguous_result = json.loads(
            runtime.evaluate_graph_json(json.dumps(ambiguous_graph))
        )
        if ambiguous_result["errors"]:
            raise AssertionError(
                "Runtime Python attribute lookup failed: " + json.dumps(ambiguous_result["errors"])
            )
    finally:
        if previous_definition is None:
            runtime._CATALOG.pop(ambiguous_id, None)
        else:
            runtime._CATALOG[ambiguous_id] = previous_definition
        if previous_callable is None:
            runtime._CATALOG_CALLABLES.pop(ambiguous_id, None)
        else:
            runtime._CATALOG_CALLABLES[ambiguous_id] = previous_callable

    # Instance-method receivers must remain connected even when a dotted
    # expression also resolves through the imported API namespace. This is the
    # upstream ShapeList.group_by regression (both Edge and Vertex collections).
    grouped_graph = runtime.import_python_code(
        "from build123d import *\n"
        "with BuildPart() as part:\n"
        "    Box(10, 10, 10)\n"
        "fender_edges = part.part.edges()\n"
        "edge_groups = fender_edges.group_by(Axis.Z)\n"
        "vertex_groups = part.part.vertices().group_by(Axis.Z)\n"
    )
    group_nodes = [
        node for node in grouped_graph["nodes"]
        if node["data"]["definition"]["id"].rsplit(".", 1)[-1] == "group_by"
    ]
    if len(group_nodes) != 2:
        raise AssertionError("Expected both ShapeList.group_by calls in imported graph")
    for node in group_nodes:
        receiver = next(
            (socket for socket in node["data"]["definition"]["inputs"]
             if socket["name"] in {"self", "cls"}),
            None,
        )
        if receiver is None:
            raise AssertionError("Imported group_by method has no receiver socket")
        if receiver.get("data_access") != "structure" or receiver.get("data_structure") != "list":
            raise AssertionError("ShapeList methods must consume the collection as a whole")
        if not any(
            edge["target"] == node["id"] and edge["targetHandle"] == receiver["name"]
            for edge in grouped_graph["edges"]
        ):
            raise AssertionError("Imported group_by method lost its receiver edge")
    grouped_result = json.loads(runtime.evaluate_graph_json(json.dumps(grouped_graph)))
    if grouped_result["errors"]:
        raise AssertionError(
            "Imported ShapeList.group_by workflows failed: " + json.dumps(grouped_result["errors"])
        )

    public_catalog = json.loads(runtime.catalog_json())
    if any(item["id"] in runtime._INTERNAL_NODE_IDS for item in public_catalog):
        raise AssertionError("Python graph-lowering operations leaked into the public catalog")

    try:
        runtime.import_python_code("import arbitrary_package\n")
    except ValueError as exc:
        if "arbitrary_package" not in str(exc):
            raise
    else:
        raise AssertionError("Importer unexpectedly allowed an unrelated third-party import")

    try:
        runtime.import_python_code("from build123d import Box\nwhile False:\n    Box(1, 1, 1)\n")
    except ValueError as exc:
        if "Unsupported statement" not in str(exc):
            raise
    else:
        raise AssertionError("Importer silently accepted unsupported control flow")

    return {
        "name": "Python source to executable graph",
        "viewer_import_forms": 3,
        "constructor_nodes": 2,
        "native_operator": True,
        "graph_edges": 2,
        "evaluation": True,
        "unsupported_syntax_rejected": True,
    }


def run_instance_method_receiver_tests():
    """Calls on geometry collections must retain their instance receiver edge."""
    subscript = runtime._CATALOG["core.python.subscript"]
    value_socket = next(
        socket for socket in subscript["inputs"] if socket["name"] == "value"
    )
    if value_socket.get("data_access") != "structure":
        raise AssertionError("Python subscript must consume collections as structures")
    graph = runtime.import_python_code(
        "from build123d import *\n"
        "with BuildPart() as part:\n"
        "    Box(10, 10, 10)\n"
        "fender_edges = part.part.edges()\n"
        "edge_groups = fender_edges.group_by(Axis.Z)\n"
        "vertex_groups = part.part.vertices().group_by(Axis.Z)\n"
    )
    method_nodes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"].endswith(".group_by")
    ]
    if len(method_nodes) != 2:
        raise AssertionError("Expected two collection group_by nodes: " + repr(method_nodes))
    for node in method_nodes:
        definition = node["data"]["definition"]
        receiver = next(
            (socket for socket in definition.get("inputs", [])
             if socket.get("name") in {"self", "cls"}), None
        )
        incoming = [
            edge for edge in graph["edges"]
            if edge.get("target") == node["id"]
        ]
        if receiver is None or not any(
            edge.get("targetHandle") == receiver["name"] for edge in incoming
        ):
            raise AssertionError(
                "Collection method lost its receiver edge: "
                + repr({"node": node, "incoming": incoming})
            )
    result = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if result["errors"]:
        raise AssertionError("Collection instance methods failed: " + json.dumps(result["errors"]))
    return {"name": "instance method receivers", "methods": len(method_nodes)}


def run_python_scope_tests():
    """BuildPart and nested Locations retain real context-manager lifecycles."""
    source = (
        "from build123d import BuildPart, Locations, Box, Cylinder, Mode\n"
        "with BuildPart(mode=Mode.ADD) as part:\n"
        "    with Locations((0, 0, 0), (12, 0, 0)):\n"
        "        Box(4, 4, 4)\n"
        "        Cylinder(2, 6)\n"
        "result = part.part\n"
    )
    graph = runtime.import_python_code(source)
    scopes = graph.get("execution_scopes", [])
    if [item["name"] for item in scopes] != ["BuildPart", "Locations"]:
        raise AssertionError("Nested context managers were not represented: " + json.dumps(scopes))
    scope_paths = [
        node["data"].get("execution_scope_path", [])
        for node in graph["nodes"]
        if node["data"].get("definition", {}).get("name") in {"Box", "Cylinder"}
    ]
    if not scope_paths or any(len(path) != 2 for path in scope_paths):
        raise AssertionError("Geometry operations lost their nested execution scope: " + repr(scope_paths))

    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"]:
        raise AssertionError("Nested build123d context execution failed: " + json.dumps(evaluated["errors"]))
    result_nodes = [
        node["id"] for node in graph["nodes"]
        if node["data"].get("scope_result")
    ]
    if not result_nodes or result_nodes[-1] not in evaluated["previews"]:
        raise AssertionError("BuildPart scope result did not produce a geometry preview")

    exported = runtime.export_python_code(graph)
    compile(exported, "<scoped-build123d-export>", "exec")
    if "with b3d.BuildPart(" not in exported or "with b3d.Locations(" not in exported:
        raise AssertionError("Python export dropped nested context managers: " + exported)
    if "_scope_1.part" not in exported or "b3d.Mode.ADD" not in exported:
        raise AssertionError("Python export did not preserve builder result/configuration")

    sketch_source = (
        "from build123d import BuildSketch, Plane, Rectangle\n"
        "with BuildSketch(Plane.XY):\n"
        "    Rectangle(6, 8)\n"
    )
    sketch_graph = runtime.import_python_code(sketch_source)
    encoded_plane = sketch_graph["execution_scopes"][0]["args"][0]
    if encoded_plane.get("__flow_object__") != "Plane":
        raise AssertionError("Plane.XY was not encoded as a portable scope value: " + repr(encoded_plane))
    sketch_result = json.loads(runtime.evaluate_graph_json(json.dumps(sketch_graph)))
    if sketch_result["errors"]:
        raise AssertionError("BuildSketch(Plane.XY) scope failed: " + json.dumps(sketch_result["errors"]))
    sketch_export = runtime.export_python_code(sketch_graph)
    compile(sketch_export, "<buildsketch-export>", "exec")
    if "b3d.Plane(" not in sketch_export:
        raise AssertionError("BuildSketch plane constant was not restored by export")

    constructed_plane = runtime.import_python_code(
        "from build123d import BuildSketch, Plane, Rectangle\n"
        "with BuildSketch(Plane(origin=(0, 0, 0), x_dir=(1, 0, 0), z_dir=(0, 0, 1))):\n"
        "    Rectangle(3, 5)\n"
    )
    plane_argument = constructed_plane["execution_scopes"][0]["args"][0]
    if plane_argument.get("__flow_object__") != "Plane" or "kwargs" not in plane_argument:
        raise AssertionError("Plane(...) context argument lost constructor semantics")
    if json.loads(runtime.evaluate_graph_json(json.dumps(constructed_plane)))["errors"]:
        raise AssertionError("BuildSketch(Plane(...)) failed")

    multi = runtime.import_python_code(
        "from build123d import BuildPart, Locations, Box\n"
        "with BuildPart() as part, Locations((0, 0, 0), (8, 0, 0)):\n"
        "    Box(2, 2, 2)\n"
    )
    if [item["name"] for item in multi["execution_scopes"]] != ["BuildPart", "Locations"]:
        raise AssertionError("Multiple managers in one with-statement were not nested")
    if json.loads(runtime.evaluate_graph_json(json.dumps(multi)))["errors"]:
        raise AssertionError("Multiple-manager with-statement failed")

    return {"name": "Python execution scopes", "nested_contexts": True,
            "builder_result": True, "enum_and_plane_values": True, "export": True}


def run_python_export_tests():
    box = _definition("Box")
    cylinder = _definition("Cylinder")
    union = _shape_operator("__add__")
    union_inputs = [socket["name"] for socket in union["inputs"]]
    nodes = [
        _node(box, "box_a", {"length": 12, "width": 12, "height": 12}),
        _node(cylinder, "cylinder_b", {"radius": 5, "height": 16}),
        _node(union, "union"),
    ]
    edges = [
        _edge("box_a", "union", union_inputs[0]),
        _edge("cylinder_b", "union", union_inputs[1]),
    ]
    source = runtime.export_python_code(json.dumps({"nodes": nodes, "edges": edges}))
    compile(source, "<build123d-flow-export>", "exec")
    if "import build123d as b3d" not in source:
        raise AssertionError("Python export is missing the build123d import")
    if "b3d.Box(" not in source or "b3d.Cylinder(" not in source:
        raise AssertionError("Python export did not preserve native build123d constructors")
    if "_node_1 + _node_2" not in source or "result = _node_3" not in source:
        raise AssertionError("Python export did not preserve the native Shape operator graph")
    if "flow.boolean." in source:
        raise AssertionError("Python export still references app-authored Boolean wrappers")

    unsupported = _core_node("core.math.add", "add")
    try:
        runtime.export_python_code(json.dumps({"nodes": [unsupported], "edges": []}))
    except ValueError as exc:
        if "Unsupported nodes" not in str(exc):
            raise
    else:
        raise AssertionError("Python export silently accepted an unsupported Flow operation")
    return {"name": "Python export", "native_constructors": True, "native_operators": True}


def run_graph_plan_validation_tests():
    nodes = [
        {"id": "left", "data": {}},
        {"id": "right", "data": {}},
        {"id": "target", "data": {}},
    ]
    edges = [
        {"id": "first", "source": "left", "target": "target",
         "sourceHandle": "result", "targetHandle": "value"},
        {"id": "second", "source": "right", "target": "target",
         "sourceHandle": "result", "targetHandle": "value"},
    ]
    graph = {"nodes": nodes, "edges": edges}

    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"].get("graph") != "Input 'target.value' has multiple connections":
        raise AssertionError("Evaluator did not reject duplicate input connections")

    try:
        runtime.export_python_code(graph)
    except ValueError as exc:
        if str(exc) != "Input 'target.value' has multiple connections":
            raise
    else:
        raise AssertionError("Python exporter did not reject duplicate input connections")

    cyclic_graph = {
        "nodes": [{"id": "left", "data": {}}, {"id": "right", "data": {}}],
        "edges": [
            {"id": "left-right", "source": "left", "target": "right",
             "sourceHandle": "result", "targetHandle": "value"},
            {"id": "right-left", "source": "right", "target": "left",
             "sourceHandle": "result", "targetHandle": "value"},
        ],
    }
    cyclic_result = json.loads(runtime.evaluate_graph_json(json.dumps(cyclic_graph)))
    if cyclic_result["errors"].get("graph") != "Graph contains a cycle":
        raise AssertionError("Evaluator did not reject cyclic graphs")
    try:
        runtime.export_python_code(cyclic_graph)
    except ValueError as exc:
        if str(exc) != "Graph contains a cycle":
            raise
    else:
        raise AssertionError("Python exporter did not reject cyclic graphs")

    return {"name": "graph plan validation", "duplicate_inputs": True, "cycles": True}


def run_operator_mro_resolution_tests():
    class FlowTestRoot:
        pass

    class FlowTestBase(FlowTestRoot):
        pass

    class FlowTestLeaf(FlowTestBase):
        pass

    class FlowTestUnrelated:
        pass

    previous = runtime._DISCOVERED_CLASSES
    runtime._DISCOVERED_CLASSES = {
        "FlowTestRoot": (FlowTestRoot,),
        "FlowTestBase": (FlowTestBase,),
        "FlowTestLeaf": (FlowTestLeaf,),
        "FlowTestUnrelated": (FlowTestUnrelated,),
    }
    try:
        definitions = {
            "fixture.FlowTestRoot.operator.__add__": {
                "inputs": [{"name": "self", "type": "FlowTestRoot"}],
            },
            "fixture.FlowTestBase.operator.__add__": {
                "inputs": [{"name": "self", "type": "FlowTestBase"}],
            },
        }
        selected, winners = runtime._resolve_catalog_member(
            definitions, "__add__", "FlowTestLeaf"
        )
        if selected != "fixture.FlowTestBase.operator.__add__" or winners != (selected,):
            raise AssertionError(
                "Member resolution did not select the nearest Python MRO owner: "
                + repr((selected, winners))
            )
        if runtime._receiver_type_distance(
            "FlowTestLeaf", ["FlowTestRoot"]
        ) != 2:
            raise AssertionError("Root receiver distance did not follow Python's MRO")
        if runtime._receiver_type_distance(
            "FlowTestLeaf", ["FlowTestUnrelated"]
        ) is not None:
            raise AssertionError("An unrelated receiver class was treated as compatible")
    finally:
        runtime._DISCOVERED_CLASSES = previous

    return {"name": "operator receiver MRO", "nearest_owner": True, "unrelated_rejected": True}


def run_operator_overload_tests():
    graph = runtime.import_python_code(
        "from build123d import Circle, Plane\n"
        "plane = Plane(origin=(0, 0, 0))\n"
        "circle = Circle(2)\n"
        "result = plane * circle\n"
    )
    binary_nodes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.binary.__mul__"
    ]
    if len(binary_nodes) != 1:
        raise AssertionError("Plane * Circle must lower to one native multiplication node")
    definition = binary_nodes[0]["data"]["definition"]
    inputs = {socket["name"]: socket for socket in definition.get("inputs", [])}
    if inputs.get("right", {}).get("data_access") != "item":
        raise AssertionError(
            "A scalar Shape overload must consume one item, not the iterable union: "
            + repr(inputs.get("right"))
        )
    output = definition.get("outputs", [{}])[0]
    if output.get("data_structure") in {"list", "tree", "tuple", "set"}:
        raise AssertionError(
            "Plane * Circle must retain its scalar Shape return contract: " + repr(output)
        )
    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"]:
        raise AssertionError("Plane * Circle native operator evaluation failed: " + json.dumps(evaluated["errors"]))
    return {"name": "operator overload selection", "scalar_shape_overload": True}


def run_geometry_subclass_constructor_tests():
    graph = runtime.import_python_code(
        "from build123d import *\n"
        "class ProceduralPart(Part):\n"
        "    def __init__(self, length, width, height, radius=0.0):\n"
        "        obj = extrude(Rectangle(length, width), amount=height)\n"
        "        if radius != 0.0:\n"
        "            obj = fillet(obj.edges(), radius=radius)\n"
        "        obj -= Rot(0, 90, 0) * Cylinder(width / 4, length)\n"
        "        super().__init__(obj.wrapped)\n"
        "part = ProceduralPart(10, 8, 6, 0.2)\n"
    )
    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"]:
        raise AssertionError("A statically lowered geometry subclass failed: " + json.dumps(evaluated["errors"]))
    if not graph["nodes"]:
        raise AssertionError("Geometry subclass lowering produced no native graph operations")
    terminal = graph["nodes"][-1]["id"]
    if terminal not in evaluated["previews"]:
        raise AssertionError("Geometry subclass base initialization produced no geometry preview")
    builder_graph = runtime.import_python_code(
        "from build123d import *\n"
        "class BuilderPart(Solid):\n"
        "    def __init__(self, length, width, height):\n"
        "        with BuildPart() as obj:\n"
        "            with BuildSketch():\n"
        "                Rectangle(length, width)\n"
        "            extrude(amount=height)\n"
        "            Cylinder(width / 4, length, rotation=(0, 90, 0), mode=Mode.SUBTRACT)\n"
        "        super().__init__(obj.part.wrapped)\n"
        "part = BuilderPart(10, 8, 6)\n"
    )
    builder_result = json.loads(runtime.evaluate_graph_json(json.dumps(builder_graph)))
    if builder_result["errors"]:
        raise AssertionError("A builder-based geometry subclass failed: " + json.dumps(builder_result["errors"]))
    if builder_graph["nodes"][-1]["id"] not in builder_result["previews"]:
        raise AssertionError("Builder subclass base initialization produced no geometry preview")
    return {"name": "geometry subclass constructor lowering", "native_base_initialized": True}


def run_empty_shape_preview_tests():
    empty_curve = runtime.b3d.Curve()
    if not empty_curve.wrapped.IsNull():
        raise AssertionError("Curve() should be represented by a null intermediate shape")
    if runtime._mesh_preview(empty_curve) is not None:
        raise AssertionError("An empty intermediate shape must not produce a mesh preview")

    # Wire() wraps an empty TopoDS_Compound: wrapped.IsNull() is false even
    # though build123d correctly reports the geometry as empty.
    empty_wire = runtime.b3d.Wire()
    if not empty_wire.is_empty:
        raise AssertionError("Wire() should be represented by an empty geometry value")
    if runtime._mesh_preview(empty_wire) is not None:
        raise AssertionError("An empty compound must not be sent to tessellation")

    solid_box = runtime.b3d.Box(1, 1, 1)
    if runtime._mesh_preview(solid_box) is None:
        raise AssertionError("A non-empty Box must still produce a mesh preview")
    return {
        "name": "empty intermediate geometry preview",
        "null_and_empty_shapes_skipped": True,
        "non_empty_box_preserved": True,
    }


def run_constructor_overload_tests():
    graph = runtime.import_python_code(
        "from build123d import Curve, Line, Wire\n"
        "profile = Curve() + (\n"
        "    Line((0, 0), (10, 0)),\n"
        "    Line((10, 0), (10, 10)),\n"
        ")\n"
        "profile_edge_count = len(profile.edges())\n"
        "wire = Wire(profile.edges())\n"
        "result = len(wire.edges())\n"
    )
    wire_nodes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"].startswith(
            "build123d.topology.one_d.Wire.overload-"
        )
    ]
    if len(wire_nodes) != 1:
        raise AssertionError("Wire(profile.edges()) must resolve to one constructor overload")
    selected = wire_nodes[0]["data"]["definition"]["id"]
    if not selected.endswith(".overload-5"):
        raise AssertionError(
            "A ShapeList[Edge] argument must select Wire(Iterable[Edge]), not "
            + selected
        )
    evaluated = json.loads(runtime.evaluate_graph_json(json.dumps(graph)))
    if evaluated["errors"]:
        raise AssertionError("Wire iterable constructor evaluation failed: " + json.dumps(evaluated["errors"]))
    count_nodes = [
        node for node in graph["nodes"]
        if node["data"]["definition"]["id"] == "core.python.builtin.len"
    ]
    if len(count_nodes) != 2:
        raise AssertionError("Expected separate profile and Wire edge-count nodes")
    profile_count = evaluated["socket_values"][count_nodes[0]["id"]]["result"]
    wire_count = evaluated["socket_values"][count_nodes[1]["id"]]["result"]
    if profile_count.get("value") != 2:
        raise AssertionError("Curve addition did not preserve both profile edges: " + repr(profile_count))
    if wire_count.get("value") != 2:
        raise AssertionError("Wire(profile.edges()) must retain both edges: " + repr(wire_count))
    return {"name": "constructor overload selection", "wire_iterable_edges": True}


def run_lib3mf_binding_tests():
    import os

    mesher = runtime.b3d.Mesher()
    version = mesher.library_version
    if not version.startswith("2.5.0"):
        raise AssertionError("Expected the staged upstream Lib3MF 2.5.0 binding, got " + version)

    path = "/tmp/build123d-flow-lib3mf-roundtrip.3mf"
    exporter = runtime.b3d.Mesher()
    exporter.add_shape([runtime.b3d.Box(2, 3, 4)])
    exporter.write(path)
    try:
        imported = runtime.b3d.Mesher().read(path)
    finally:
        if os.path.exists(path):
            os.remove(path)
    if len(imported) != 1 or imported[0].volume <= 0:
        raise AssertionError("Upstream Mesher failed to round-trip a solid through 3MF")
    return {"name": "upstream Lib3MF binding", "version": version, "three_mf_round_trip": True}


def run_graph_workflow_tests():
    results = [run_graph_plan_validation_tests(), run_catalog_metadata_tests(), run_annotation_resolution_tests(), run_collection_workflow_tests(), run_data_mapping_tests(), run_dataflow_shape_inference_tests(), run_operator_mro_resolution_tests(), run_operator_overload_tests(), run_geometry_subclass_constructor_tests(), run_empty_shape_preview_tests(), run_constructor_overload_tests(), run_lib3mf_binding_tests(), run_shape_collection_workflow_test(), run_preview_style_test(), run_python_import_tests(), run_python_loop_lowering_tests(), run_python_value_constructor_tests(), run_instance_method_receiver_tests(), run_python_scope_tests(), run_python_export_tests()]

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
