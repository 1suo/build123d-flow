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


def _is_candidate(obj, module_name):
    if not (inspect.isclass(obj) or inspect.isfunction(obj) or inspect.isbuiltin(obj)):
        return False
    if getattr(obj, "__module__", "").split(".")[0] != "build123d":
        return False
    if getattr(obj, "__name__", "").startswith("_"):
        return False
    try:
        signature = inspect.signature(obj)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            return False
    return True


def _discover_modules():
    modules = [b3d]
    seen = {b3d.__name__}
    try:
        for info in pkgutil.walk_packages(b3d.__path__, b3d.__name__ + "."):
            if info.name in seen:
                continue
            seen.add(info.name)
            try:
                modules.append(importlib.import_module(info.name))
            except Exception:
                # Optional modules may rely on unavailable native/system features.
                continue
    except Exception:
        pass
    return modules


def discover():
    global _CATALOG, _CATALOG_CALLABLES
    found = {}
    callables = {}
    shape_classes = set()
    for module in _discover_modules():
        module_name = getattr(module, "__name__", "build123d")
        for exported_name, obj in vars(module).items():
            if not _is_candidate(obj, module_name):
                continue
            if inspect.isclass(obj):
                try:
                    if issubclass(obj, getattr(b3d, "Shape", object)):
                        shape_classes.add(obj)
                except TypeError:
                    pass
            # Avoid exposing imported aliases repeatedly; retain canonical API path.
            canonical_module = getattr(obj, "__module__", module_name)
            name = getattr(obj, "__name__", exported_name)
            node_id = canonical_module + "." + name
            if node_id in found:
                continue
            try:
                signature = inspect.signature(obj)
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
                if parameter.kind == parameter.POSITIONAL_ONLY:
                    unsupported = True
                    break
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
            if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD, p.POSITIONAL_ONLY) for p in parameters):
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

    _CATALOG = found
    _CATALOG_CALLABLES = callables
    return list(found.values())


def catalog_json():
    if not _CATALOG:
        discover()
    return json.dumps(list(_CATALOG.values()))


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
            preview = _mesh_preview(result)
            if preview is not None:
                previews[node_id] = preview
                _LAST_SHAPE = result
        except Exception as exc:
            errors[node_id] = type(exc).__name__ + ": " + str(exc)

    _LAST_VALUES = values
    return json.dumps({"previews": previews, "errors": errors, "order": order})



def _call_with_coercion(callable_obj, params):
    target = callable_obj.__init__ if inspect.isclass(callable_obj) else callable_obj
    try:
        hints = typing.get_type_hints(target)
    except Exception:
        hints = {}
    converted = dict(params)
    for name, value in list(converted.items()):
        annotation = hints.get(name)
        if annotation is None:
            try:
                annotation = inspect.signature(callable_obj).parameters[name].annotation
            except Exception:
                annotation = None
        if isinstance(value, str) and inspect.isclass(annotation):
            try:
                if issubclass(annotation, enum.Enum):
                    converted[name] = annotation[value]
            except (TypeError, KeyError):
                pass
    return callable_obj(**converted)

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


discover()
