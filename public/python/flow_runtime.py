"""Introspection and graph execution inside the browser's Pyodide runtime."""
from __future__ import annotations

import enum
import importlib
import inspect
import json
import pkgutil
import typing
import types

import build123d as b3d

_CATALOG = {}
_CATALOG_CALLABLES = {}
_LAST_VALUES = {}
_LAST_SHAPE = None
_DISCOVERY_REPORT = {}


def _type_name(annotation):
    if annotation is inspect.Signature.empty:
        return "Any"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        names = {_type_name(arg) for arg in args if arg is not type(None)}
        return names.pop() if len(names) == 1 else "Any"
    if origin in (list, tuple, set):
        return "List"
    if inspect.isclass(annotation):
        try:
            if issubclass(annotation, enum.Enum):
                return "Enum"
            for shape_name in ("Solid", "Compound", "Face", "Edge", "Wire"):
                shape_class = getattr(b3d, shape_name, None)
                if inspect.isclass(shape_class) and issubclass(annotation, shape_class):
                    return shape_name
            if issubclass(annotation, getattr(b3d, "Shape", object)):
                return "Shape"
        except TypeError:
            pass
    name = getattr(annotation, "__name__", str(annotation)).lower()
    mapping = {
        "int": "Number", "float": "Number", "bool": "Boolean",
        "str": "String", "vector": "Vector3", "plane": "Plane",
        "location": "Location", "shape": "Shape", "solid": "Solid",
        "compound": "Compound", "face": "Face", "edge": "Edge",
        "wire": "Wire",
    }
    return mapping.get(name, "Any")


def _json_default(value):
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _safe_default(value):
    if value is inspect.Signature.empty:
        return None, None, True, False
    # Only expose defaults that can safely round-trip through JSON and back
    # into Python without guessing at CAD object / enum reconstruction.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value, repr(value), False, True
    return None, repr(value), False, False


def _api_signature(obj):
    """Return the callable signature, using __init__ for classes when needed."""
    try:
        signature = inspect.signature(obj)
    except (TypeError, ValueError):
        signature = None
    if inspect.isclass(obj):
        try:
            init_signature = inspect.signature(obj.__init__)
            parameters = list(init_signature.parameters.values())
            if parameters and parameters[0].name in ("self", "cls"):
                init_signature = init_signature.replace(parameters=parameters[1:])
            # A generic *args/**kwargs class signature hides a useful __init__ signature.
            useful = [p for p in init_signature.parameters.values()
                      if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
            if useful or signature is None:
                return init_signature
        except (TypeError, ValueError, AttributeError):
            pass
    if signature is None:
        raise ValueError("No inspectable signature")
    return signature


def _candidate_rejection(obj):
    if not (inspect.isclass(obj) or inspect.isfunction(obj) or inspect.isbuiltin(obj)):
        return "not a class/function/builtin"
    if getattr(obj, "__module__", "").split(".")[0] != "build123d":
        return "not defined in build123d"
    if getattr(obj, "__name__", "").startswith("_"):
        return "private name"
    try:
        _api_signature(obj)
    except (TypeError, ValueError) as exc:
        return "signature unavailable: " + str(exc)
    return None


def _is_candidate(obj, module_name):
    return _candidate_rejection(obj) is None


def _discover_modules(report=None):
    modules = [b3d]
    seen = {b3d.__name__}
    report = report if report is not None else {}
    report["module_import_failures"] = []
    report["module_walk_error"] = None
    try:
        for info in pkgutil.walk_packages(b3d.__path__, b3d.__name__ + "."):
            if info.name in seen:
                continue
            seen.add(info.name)
            try:
                modules.append(importlib.import_module(info.name))
            except Exception as exc:
                report["module_import_failures"].append({
                    "module": info.name, "error": type(exc).__name__ + ": " + str(exc)
                })
    except Exception as exc:
        report["module_walk_error"] = type(exc).__name__ + ": " + str(exc)
    report["modules_visited"] = [getattr(module, "__name__", str(module)) for module in modules]
    return modules




def _boolean_union(a, b):
    return a + b


def _boolean_difference(a, b):
    return a - b


def _boolean_intersection(a, b):
    return a & b



def _add_node(found, callables, node_id, name, category, function=None, inputs=None, output_type=None, description=""):
    # Allow concise calls without an explicit category; infer it from the stable node id.
    if output_type is None and callable(category):
        output_type = inputs
        inputs = function
        function = category
        category = node_id.split(".")[1].title() if len(node_id.split(".")) > 2 else "Core"
    found[node_id] = {
        "id": node_id,
        "kind": "value" if not inputs else "operation",
        "name": name,
        "category": category,
        "description": description,
        "inputs": [
            {"name": key, "type": socket_type, "annotation": socket_type, "required": True}
            for key, socket_type in inputs
        ],
        "outputs": [{"name": "result", "type": output_type, "annotation": output_type}],
    }
    callables[node_id] = function


def _add_core_nodes(found, callables):
    import math
    import operator

    # Editable literal sources make primitive values available without an upstream node.
    for node_id, name, socket_type, default, fn in [
        ("core.value.number", "Number", "Number", 0.0, lambda value: float(value)),
        ("core.value.string", "String", "String", "", lambda value: str(value)),
        ("core.value.boolean", "Boolean", "Boolean", False, lambda value: bool(value)),
    ]:
        found[node_id] = {
            "id": node_id, "kind": "value", "name": name, "category": "Values",
            "description": "Literal " + socket_type.lower() + " value",
            "inputs": [{"name": "value", "type": socket_type, "annotation": socket_type,
                        "required": False, "default": default}],
            "outputs": [{"name": "result", "type": socket_type, "annotation": socket_type}],
        }
        callables[node_id] = fn

    # Scalar arithmetic and comparisons.
    numeric_binary = [
        ("add", "Add", lambda a, b: operator.add(a, b)), ("subtract", "Subtract", lambda a, b: operator.sub(a, b)),
        ("multiply", "Multiply", lambda a, b: operator.mul(a, b)), ("divide", "Divide", lambda a, b: operator.truediv(a, b)),
        ("floor_divide", "Floor Divide", lambda a, b: operator.floordiv(a, b)), ("modulo", "Modulo", lambda a, b: operator.mod(a, b)),
        ("power", "Power", lambda a, b: operator.pow(a, b)),
    ]
    for key, label, fn in numeric_binary:
        _add_node(found, callables, "core.math." + key, label, "Math", fn,
                  [("a", "Number"), ("b", "Number")], "Number")
    for key, label, fn in [
        ("negate", "Negate", lambda value: operator.neg(value)), ("absolute", "Absolute", lambda value: abs(value)),
        ("sqrt", "Square Root", math.sqrt), ("sin", "Sin", math.sin),
        ("cos", "Cos", math.cos), ("tan", "Tan", math.tan),
        ("asin", "Asin", math.asin), ("acos", "Acos", math.acos),
        ("atan", "Atan", math.atan), ("radians", "Radians", math.radians),
        ("degrees", "Degrees", math.degrees), ("exp", "Exp", math.exp),
        ("log", "Log", math.log10), ("ln", "Natural Log", math.log),
        ("floor", "Floor", math.floor), ("ceil", "Ceil", math.ceil),
        ("round", "Round", round),
    ]:
        _add_node(found, callables, "core.math." + key, label, "Math",
                  lambda value, _fn=fn: _fn(value), [("value", "Number")], "Number")
    for key, label, value in [("pi", "Pi", math.pi), ("e", "E", math.e), ("tau", "Tau", math.tau)]:
        _add_node(found, callables, "core.constant." + key, label, "Constants",
                  lambda _value=value: _value, [], "Number", "Mathematical constant")

    for key, label, fn in [
        ("minimum", "Minimum", lambda a, b: min(a, b)), ("maximum", "Maximum", lambda a, b: max(a, b)),
        ("clamp", "Clamp", lambda value, low, high: max(low, min(high, value))),
    ]:
        args = [("a", "Number"), ("b", "Number")] if key != "clamp" else [
            ("value", "Number"), ("low", "Number"), ("high", "Number")]
        _add_node(found, callables, "core.math." + key, label, "Math", fn, args, "Number")

    for key, label, fn in [
        ("equal", "Equal", lambda a, b: operator.eq(a, b)), ("not_equal", "Not Equal", lambda a, b: operator.ne(a, b)),
        ("less", "Less Than", lambda a, b: operator.lt(a, b)), ("less_equal", "Less or Equal", lambda a, b: operator.le(a, b)),
        ("greater", "Greater Than", lambda a, b: operator.gt(a, b)), ("greater_equal", "Greater or Equal", lambda a, b: operator.ge(a, b)),
    ]:
        _add_node(found, callables, "core.compare." + key, label, "Logic",
                  fn, [("a", "Number"), ("b", "Number")], "Boolean")
    _add_node(found, callables, "core.logic.and", "And", lambda a, b: operator.and_(a, b),
              [("a", "Boolean"), ("b", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.or", "Or", lambda a, b: operator.or_(a, b),
              [("a", "Boolean"), ("b", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.not", "Not", lambda value: operator.not_(value),
              [("value", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.select", "Select", lambda condition, if_true, if_false: if_true if condition else if_false,
              [("condition", "Boolean"), ("if_true", "Any"), ("if_false", "Any")], "Any")

    # String construction and common operations.
    _add_node(found, callables, "core.string.join", "Join", lambda a, b, separator: str(a) + separator + str(b),
              [("a", "String"), ("b", "String"), ("separator", "String")], "String")
    _add_node(found, callables, "core.string.format", "Format", lambda template, value: template.format(value),
              [("template", "String"), ("value", "Any")], "String")
    _add_node(found, callables, "core.string.replace", "Replace", lambda value, old, new: value.replace(old, new),
              [("value", "String"), ("old", "String"), ("new", "String")], "String")
    _add_node(found, callables, "core.string.length", "String Length", lambda value: len(value),
              [("value", "String")], "Number")
    _add_node(found, callables, "core.string.upper", "Uppercase", lambda value: value.upper(),
              [("value", "String")], "String")
    _add_node(found, callables, "core.string.lower", "Lowercase", lambda value: value.lower(),
              [("value", "String")], "String")
    _add_node(found, callables, "core.convert.to_number", "To Number", lambda value: float(value),
              [("value", "Any")], "Number")
    _add_node(found, callables, "core.convert.to_string", "To String", lambda value: str(value),
              [("value", "Any")], "String")

    # Vector operations use build123d's native Vector type.
    vector = b3d.Vector
    _add_node(found, callables, "core.vector.construct", "Vector", "Vectors",
              lambda x, y, z: vector(x, y, z),
              [("x", "Number"), ("y", "Number"), ("z", "Number")], "Vector3")
    _add_node(found, callables, "core.vector.add", "Vector Add", lambda a, b: a + b,
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.subtract", "Vector Subtract", lambda a, b: a - b,
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.scale", "Vector Scale",
              lambda vector_value, factor: vector_value * factor,
              [("vector_value", "Vector3"), ("factor", "Number")], "Vector3")
    _add_node(found, callables, "core.vector.dot", "Dot Product", lambda a, b: a.dot(b),
              [("a", "Vector3"), ("b", "Vector3")], "Number")
    _add_node(found, callables, "core.vector.cross", "Cross Product",
              lambda a, b: a.cross(b),
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.length", "Vector Length",
              lambda value: value.length,
              [("value", "Vector3")], "Number")
    _add_node(found, callables, "core.vector.normalize", "Normalize",
              lambda value: value.normalized(),
              [("value", "Vector3")], "Vector3")


def _python_function(code, a=None, b=None, c=None):
    import math
    import operator
    safe_builtins = {
        "abs": abs, "min": min, "max": max, "round": round,
        "int": int, "float": float, "str": str, "bool": bool,
        "len": len, "sum": sum, "range": range, "enumerate": enumerate,
        "zip": zip, "sorted": sorted,
    }
    scope = {"__builtins__": safe_builtins, "math": math, "operator": operator,
             "a": a, "b": b, "c": c, "b3d": b3d, "Vector": b3d.Vector}
    exec(compile(code, "<node>", "exec"), scope, scope)
    if "result" not in scope:
        raise ValueError("Python Function must assign its output to 'result'")
    return scope["result"]



def discover(include_full=True):
    global _CATALOG, _CATALOG_CALLABLES
    found = {}
    callables = {}
    shape_classes = set()
    report = {
        "include_full": include_full,
        "modules_visited": [],
        "module_import_failures": [],
        "rejected": [],
        "accepted": [],
    }
    modules = _discover_modules(report) if include_full else [b3d]
    if not include_full:
        report["modules_visited"] = [b3d.__name__]
    for module in modules:
        module_name = getattr(module, "__name__", "build123d")
        for exported_name, obj in vars(module).items():
            if inspect.isclass(obj):
                try:
                    if issubclass(obj, getattr(b3d, "Shape", object)):
                        shape_classes.add(obj)
                except TypeError:
                    pass
            rejection = _candidate_rejection(obj)
            if rejection:
                if getattr(obj, "__module__", "").split(".")[0] == "build123d":
                    report["rejected"].append({
                        "module": module_name, "name": getattr(obj, "__name__", exported_name),
                        "reason": rejection,
                    })
                continue
            # Avoid exposing imported aliases repeatedly; retain canonical API path.
            canonical_module = getattr(obj, "__module__", module_name)
            name = getattr(obj, "__name__", exported_name)
            node_id = canonical_module + "." + name
            if node_id in found:
                continue
            try:
                signature = _api_signature(obj)
            except (TypeError, ValueError):
                continue

            inputs = []
            unsupported = False
            try:
                hint_target = obj.__init__ if inspect.isclass(obj) else obj
                hints = typing.get_type_hints(hint_target)
            except Exception:
                hints = {}

            for parameter in signature.parameters.values():
                if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                    continue
                if parameter.kind == parameter.POSITIONAL_ONLY:
                    unsupported = True
                    break
                default, default_repr, required, has_default = _safe_default(parameter.default)
                annotation = hints.get(parameter.name, parameter.annotation)
                socket = {
                    "name": parameter.name,
                    "kind": parameter.kind.name.lower(),
                    "type": _type_name(annotation),
                    "annotation": str(annotation) if annotation is not inspect.Signature.empty else "",
                    "required": required,
                    "default_repr": default_repr,
                }
                if has_default:
                    socket["default"] = default
                if inspect.isclass(annotation):
                    try:
                        if issubclass(annotation, enum.Enum):
                            socket["options"] = [member.name for member in annotation]
                    except TypeError:
                        pass
                inputs.append(socket)
            if unsupported:
                continue

            if inspect.isclass(obj):
                try:
                    if issubclass(obj, getattr(b3d, "Shape", object)):
                        output_type = _type_name(obj)
                        node_kind = "geometry"
                    elif issubclass(obj, enum.Enum):
                        node_kind, output_type = "enum", "Enum"
                    else:
                        node_kind, output_type = "value", _type_name(obj)
                except TypeError:
                    node_kind, output_type = "value", "Any"
            else:
                node_kind = "operation"
                output_type = _type_name(hints.get("return", signature.return_annotation))

            module_parts = canonical_module.split(".")
            category = module_parts[-1].replace("_", " ").title() if len(module_parts) > 1 else "Build123d"
            doc = inspect.getdoc(obj) or ""
            found[node_id] = {
                "id": node_id,
                "kind": node_kind,
                "name": name,
                "category": category,
                "description": doc.split("\n")[0][:180],
                "inputs": inputs,
                "outputs": [{"name": "result", "type": output_type, "annotation": ""}],
            }
            callables[node_id] = obj
            report["accepted"].append({"id": node_id, "kind": node_kind})

    # Discover useful instance methods on actual B-rep classes as operation nodes.
    # Variadic methods are intentionally omitted: they need explicit wrapper nodes.
    for cls in shape_classes:
        for method_name, method in vars(cls).items():
            if method_name.startswith("_") or not inspect.isfunction(method):
                continue
            if getattr(method, "__module__", "").split(".")[0] != "build123d":
                continue
            try:
                signature = inspect.signature(method)
            except (TypeError, ValueError):
                continue
            parameters = list(signature.parameters.values())
            if not parameters or parameters[0].name != "self":
                continue
            if any(p.kind == p.POSITIONAL_ONLY for p in parameters):
                continue
            has_variadic = any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters)
            parameters = [p for p in parameters if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
            if has_variadic and len(parameters) == 1:
                continue
            try:
                hints = typing.get_type_hints(method)
            except Exception:
                hints = {}
            inputs = [{
                "name": "self",
                "label": "shape",
                "type": _type_name(cls),
                "annotation": str(cls),
                "required": True,
            }]
            for parameter in parameters[1:]:
                default, default_repr, required, has_default = _safe_default(parameter.default)
                annotation = hints.get(parameter.name, parameter.annotation)
                socket = {
                    "name": parameter.name,
                    "type": _type_name(annotation),
                    "annotation": str(annotation) if annotation is not inspect.Signature.empty else "",
                    "required": required,
                    "default_repr": default_repr,
                }
                if has_default:
                    socket["default"] = default
                if inspect.isclass(annotation):
                    try:
                        if issubclass(annotation, enum.Enum):
                            socket["options"] = [member.name for member in annotation]
                    except TypeError:
                        pass
                inputs.append(socket)
            node_id = cls.__module__ + "." + cls.__name__ + "." + method_name
            if node_id in found:
                continue
            output_type = _type_name(hints.get("return", signature.return_annotation))
            if output_type == "Any" and method_name in {
                "fuse", "cut", "intersect", "moved", "move", "rotate",
                "translate", "scale", "mirror", "fillet", "chamfer", "clean",
            }:
                output_type = _type_name(cls)
            found[node_id] = {
                "id": node_id,
                "kind": "operation",
                "name": method_name,
                "category": cls.__name__ + " Methods",
                "description": (inspect.getdoc(method) or "").split("\n")[0][:180],
                "inputs": inputs,
                "outputs": [{"name": "result", "type": output_type, "annotation": ""}],
            }
            callables[node_id] = method

    # Core dataflow nodes are explicit wrappers over Python's math/operator/builtins APIs.
    _add_core_nodes(found, callables)


    found["core.python.function"] = {
        "id": "core.python.function",
        "kind": "operation",
        "name": "Python Function",
        "category": "Python",
        "description": "Run a small Python function body. Inputs are a, b and c; assign the output to result. math and operator are available.",
        "inputs": [
            {"name": "code", "type": "Code", "annotation": "Python source", "required": False,
             "default": "result = a + b"},
            {"name": "a", "type": "Any", "annotation": "Any", "required": False, "default": None},
            {"name": "b", "type": "Any", "annotation": "Any", "required": False, "default": None},
            {"name": "c", "type": "Any", "annotation": "Any", "required": False, "default": None},
        ],
        "outputs": [{"name": "result", "type": "Any", "annotation": "Any"}],
    }
    callables["core.python.function"] = _python_function

    # Binary wrappers expose common overloaded CSG operators as graph operations.
    for node_id, name, function in [
        ("build123d_flow.union", "Union", _boolean_union),
        ("build123d_flow.difference", "Difference", _boolean_difference),
        ("build123d_flow.intersection", "Intersection", _boolean_intersection),
    ]:
        found[node_id] = {
            "id": node_id,
            "kind": "operation",
            "name": name,
            "category": "Boolean",
            "description": "Binary solid boolean operation",
            "inputs": [
                {"name": "a", "type": "Shape", "annotation": "Shape", "required": True},
                {"name": "b", "type": "Shape", "annotation": "Shape", "required": True},
            ],
            "outputs": [{"name": "result", "type": "Shape", "annotation": "Shape"}],
        }
        callables[node_id] = function

    report["node_count"] = len(found)
    report["kind_counts"] = {}
    report["category_counts"] = {}
    for definition in found.values():
        report["kind_counts"][definition["kind"]] = report["kind_counts"].get(definition["kind"], 0) + 1
        report["category_counts"][definition["category"]] = report["category_counts"].get(definition["category"], 0) + 1
    report["required_api_presence"] = {
        name: any(item["name"] == name for item in found.values())
        for name in ("Box", "Cylinder", "Rectangle", "Circle", "extrude", "revolve")
    }
    global _DISCOVERY_REPORT
    _DISCOVERY_REPORT = report
    _CATALOG = found
    _CATALOG_CALLABLES = callables
    return list(found.values())


def discovery_report_json():
    """Return an auditable snapshot of the most recent API discovery pass."""
    return json.dumps(_DISCOVERY_REPORT, default=str)


def catalog_json():
    if not _CATALOG:
        discover(include_full=False)
    return json.dumps(list(_CATALOG.values()))


def discover_full():
    return json.dumps(discover(include_full=True))


def _mesh_preview(value):
    shape_type = getattr(b3d, "Shape", None)
    if shape_type is None or not isinstance(value, shape_type):
        return None
    try:
        vertices, triangles = value.tessellate(0.15, 0.25)
        flat_vertices = []
        for vertex in vertices:
            flat_vertices.extend([float(vertex.X), float(vertex.Y), float(vertex.Z)])
        flat_triangles = [int(index) for triangle in triangles for index in triangle]
        return {"vertices": flat_vertices, "triangles": flat_triangles}
    except Exception as exc:
        raise RuntimeError("Tessellation failed: " + str(exc))


def evaluate_graph_json(payload):
    global _LAST_VALUES, _LAST_SHAPE
    _LAST_SHAPE = None
    graph = json.loads(payload)
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    incoming = {}
    outgoing = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}

    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        source_handle, target_handle = edge.get("sourceHandle"), edge.get("targetHandle")
        if source not in nodes or target not in nodes:
            continue
        incoming.setdefault(target, {})[target_handle] = (source, source_handle)
        outgoing[source].append(target)
        indegree[target] += 1

    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    order = []
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    if len(order) != len(nodes):
        return json.dumps({
            "previews": {}, "errors": {"graph": "Graph contains a cycle"}, "order": order
        })

    values = {}
    previews = {}
    errors = {}
    for node_id in order:
        node = nodes[node_id]
        definition_id = node.get("data", {}).get("definition", {}).get("id")
        definition = _CATALOG.get(definition_id)
        if definition is None:
            errors[node_id] = "Unknown or unavailable API node: " + str(definition_id)
            continue

        params = dict(node.get("data", {}).get("params", {}))
        failed_dependency = False
        for input_name, source_ref in incoming.get(node_id, {}).items():
            source_id, output_name = source_ref
            if source_id in errors or source_id not in values:
                errors[node_id] = "Input depends on a failed node: " + source_id
                failed_dependency = True
                break
            params[input_name] = values[source_id].get(output_name)
        if failed_dependency:
            continue

        try:
            callable_obj = _resolve_callable(definition_id)
            result = _call_with_coercion(callable_obj, params)
            values[node_id] = {"result": result}
        except Exception as exc:
            errors[node_id] = type(exc).__name__ + ": " + str(exc)

    # Explicitly visible geometry nodes are previewed even when they feed other nodes.
    # Older graph documents without a visibility flag retain terminal-output behavior.
    source_nodes = {edge.get("source") for edge in edges}
    for node_id in order:
        if node_id not in values:
            continue
        visible = nodes[node_id].get("data", {}).get("visible")
        if visible is False or (visible is not True and node_id in source_nodes):
            continue
        result = values[node_id].get("result")
        try:
            preview = _mesh_preview(result)
            if preview is not None:
                previews[node_id] = preview
                _LAST_SHAPE = result
        except Exception as exc:
            errors[node_id] = "Preview: " + str(exc)

    _LAST_VALUES = values
    return json.dumps({"previews": previews, "errors": errors, "order": order})



def _call_with_coercion(callable_obj, params):
    target = callable_obj.__init__ if inspect.isclass(callable_obj) else callable_obj
    try:
        hints = typing.get_type_hints(target)
    except Exception:
        hints = {}
    try:
        signature = _api_signature(callable_obj)
    except (TypeError, ValueError):
        signature = inspect.signature(callable_obj)
    converted = dict(params)
    for name, value in list(converted.items()):
        annotation = hints.get(name)
        if annotation is None:
            parameter = signature.parameters.get(name)
            annotation = parameter.annotation if parameter else None
        if isinstance(value, str) and inspect.isclass(annotation):
            try:
                if issubclass(annotation, enum.Enum):
                    converted[name] = annotation[value]
            except (TypeError, KeyError):
                pass

    positional = []
    keywords = {}
    for name, parameter in signature.parameters.items():
        if name not in converted:
            continue
        value = converted[name]
        if parameter.kind == parameter.POSITIONAL_ONLY:
            positional.append(value)
        elif parameter.kind == parameter.VAR_POSITIONAL:
            positional.extend(value if isinstance(value, (list, tuple)) else [value])
        elif parameter.kind == parameter.VAR_KEYWORD:
            if isinstance(value, dict):
                keywords.update(value)
        else:
            keywords[name] = value
    return callable_obj(*positional, **keywords)

def _resolve_callable(path):
    if path in _CATALOG_CALLABLES:
        return _CATALOG_CALLABLES[path]
    module_name, attr_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def export_step_bytes():
    if _LAST_SHAPE is None:
        return None
    import io
    stream = io.BytesIO()
    b3d.export_step(_LAST_SHAPE, stream)
    return stream.getvalue()


discover(include_full=False)
