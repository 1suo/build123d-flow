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
                "from build123d import " + class_name + " as ValueCtor\\n"
                + "ValueSink" + str(index) + "(value=" + constructor + ")\\n"
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
    return {"name": "Python value constructors", "types": results}


def run_python_import_tests():
    """Viewer-only ocp_vscode imports/calls must not block graph conversion."""
    examples = [
        "from build123d import Box\\nfrom ocp_vscode import show\\nbox = Box(10, 10, 10)\\nshow(box)\\n",
        "import build123d as b3d\\nimport ocp_vscode as viewer\\nbox = b3d.Box(10, 10, 10)\\nviewer.show(box)\\n",
        "from build123d import Box\\nfrom ocp_vscode import *\\nbox = Box(10, 10, 10)\\nshow(box)\\n",
    ]
    for source in examples:
        graph = runtime.import_python_code(source)
        if len(graph.get("nodes", [])) != 1 or graph.get("edges"):
            raise AssertionError("ocp_vscode display code changed the geometry graph: " + json.dumps(graph))

    try:
        runtime.import_python_code("import arbitrary_package\\n")
    except ValueError as exc:
        if "arbitrary_package" not in str(exc):
            raise
    else:
        raise AssertionError("Importer unexpectedly allowed an unrelated third-party import")
    return {"name": "Python import with ocp_vscode", "viewer_import_forms": 3, "viewer_calls_ignored": True}


def run_python_scope_tests():
    """BuildPart and nested Locations retain real context-manager lifecycles."""
    source = (
        "from build123d import BuildPart, Locations, Box, Cylinder, Mode\\n"
        "with BuildPart(mode=Mode.ADD) as part:\\n"
        "    with Locations((0, 0, 0), (12, 0, 0)):\\n"
        "        Box(4, 4, 4)\\n"
        "        Cylinder(2, 6)\\n"
        "result = part.part\\n"
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
    if "with b3d.BuildPart() as _scope_1:" not in exported or "with b3d.Locations(" not in exported:
        raise AssertionError("Python export dropped nested context managers: " + exported)
    if "_scope_1.part" not in exported or "b3d.Mode.ADD" not in exported:
        raise AssertionError("Python export did not preserve builder result/configuration")

    sketch_source = (
        "from build123d import BuildSketch, Plane, Rectangle\\n"
        "with BuildSketch(Plane.XY):\\n"
        "    Rectangle(6, 8)\\n"
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
        "from build123d import BuildSketch, Plane, Rectangle\\n"
        "with BuildSketch(Plane(origin=(0, 0, 0), x_dir=(1, 0, 0), z_dir=(0, 0, 1))):\\n"
        "    Rectangle(3, 5)\\n"
    )
    plane_argument = constructed_plane["execution_scopes"][0]["args"][0]
    if plane_argument.get("__flow_object__") != "Plane" or "kwargs" not in plane_argument:
        raise AssertionError("Plane(...) context argument lost constructor semantics")
    if json.loads(runtime.evaluate_graph_json(json.dumps(constructed_plane)))["errors"]:
        raise AssertionError("BuildSketch(Plane(...)) failed")

    multi = runtime.import_python_code(
        "from build123d import BuildPart, Locations, Box\\n"
        "with BuildPart() as part, Locations((0, 0, 0), (8, 0, 0)):\\n"
        "    Box(2, 2, 2)\\n"
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


def run_graph_workflow_tests():
    results = [run_graph_plan_validation_tests(), run_catalog_metadata_tests(), run_annotation_resolution_tests(), run_collection_workflow_tests(), run_data_mapping_tests(), run_dataflow_shape_inference_tests(), run_shape_collection_workflow_test(), run_preview_style_test(), run_python_import_tests(), run_python_value_constructor_tests(), run_python_scope_tests(), run_python_export_tests()]

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
