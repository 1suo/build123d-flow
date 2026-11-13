"""Introspection and graph execution inside the browser's Pyodide runtime."""
from __future__ import annotations

import enum
import importlib
import inspect
import json
import pkgutil
import typing
import types
import collections.abc
import hashlib
import operator

import build123d as b3d

_CATALOG = {}
_CATALOG_CALLABLES = {}
_CATALOG_SIGNATURES = {}
_CATALOG_HINT_TARGETS = {}
_LAST_SHAPE = None
_DISCOVERY_REPORT = {}
_EVAL_CACHE = {}
_PREVIEW_CACHE = {}
_CACHE_LIMIT = 128
# Optional curated presentation/eligibility overrides keyed by canonical node id.
_NODE_METADATA_OVERRIDES = {}


def _type_name(annotation):
    if annotation is inspect.Signature.empty or annotation is typing.Any:
        return "Any"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        union_args = [arg for arg in args if arg is not type(None)]
        shape_base = getattr(b3d, "Shape", None)
        if union_args and inspect.isclass(shape_base):
            try:
                if all(inspect.isclass(arg) and issubclass(arg, shape_base) for arg in union_args):
                    return "Shape"
            except TypeError:
                pass
        names = {_type_name(arg) for arg in union_args}
        if "Rotation" in names and any(typing.get_origin(arg) is tuple for arg in args):
            return "Rotation"
        collection_origins = {
            list, set, frozenset, collections.abc.Sequence, collections.abc.MutableSequence,
            collections.abc.Iterable, collections.abc.Collection, collections.abc.Set,
            collections.abc.MutableSet,
        }
        scalar_names = {
            _type_name(arg) for arg in args
            if arg is not type(None) and typing.get_origin(arg) not in collection_origins
        }
        collection_item_names = {
            _type_name(typing.get_args(arg)[0])
            for arg in args
            if typing.get_origin(arg) in collection_origins and typing.get_args(arg)
        }
        if len(scalar_names) == 1 and collection_item_names == scalar_names:
            return scalar_names.pop()
        return names.pop() if len(names) == 1 else "Any"
    # Preserve concrete build123d collection/value classes used as generic
    # origins (for example ShapeList[Edge]); the receiver resolver needs the
    # runtime class name to discover the next chained member.
    if inspect.isclass(origin) and getattr(origin, "__module__", "").split(".")[0] == "build123d":
        return _type_name(origin)
    if origin in (tuple, set, frozenset, list, dict, typing.Dict,
                  collections.abc.Set, collections.abc.MutableSet,
                  collections.abc.Sequence, collections.abc.MutableSequence,
                  collections.abc.Iterable, collections.abc.Collection,
                  collections.abc.Mapping, collections.abc.MutableMapping):
        return "Any"
    if origin is typing.Literal:
        return "Enum"
    if origin is typing.Annotated:
        return _type_name(args[0]) if args else "Any"
    if inspect.isclass(annotation):
        try:
            if issubclass(annotation, enum.Enum):
                return "Enum"
            for shape_name in ("Solid", "Compound", "Face", "Edge", "Wire", "Vertex", "Shell", "Sketch", "Part"):
                shape_class = getattr(b3d, shape_name, None)
                if inspect.isclass(shape_class) and issubclass(annotation, shape_class):
                    return shape_name
            if issubclass(annotation, getattr(b3d, "Shape", object)):
                return "Shape"
        except TypeError:
            pass
        if getattr(annotation, "__module__", "").split(".")[0] == "build123d":
            # Runtime class names must map to the catalog's semantic socket types.
            # build123d exposes Vector as a class; the graph contract calls it Vector3.
            return {"Vector": "Vector3"}.get(annotation.__name__, annotation.__name__)
    if isinstance(annotation, str):
        name = annotation.strip().strip("'\\\"").split(".")[-1].lower()
    else:
        name = getattr(annotation, "__name__", str(annotation)).lower()
    mapping = {
        "int": "Number", "float": "Number", "bool": "Boolean", "str": "String",
        "vector": "Vector3", "vector2": "Vector2", "vector3": "Vector3",
        "rotation": "Rotation", "plane": "Plane", "location": "Location", "axis": "Axis", "color": "Color",
        "matrix": "Matrix",
        "shape": "Shape", "solid": "Solid", "compound": "Compound", "face": "Face",
        "edge": "Edge", "wire": "Wire", "shell": "Shell", "part": "Part",
        "sketch": "Sketch", "code": "Code", "callable": "Code",
    }
    return mapping.get(name, "Any")


def _union_annotations(annotations):
    """Combine overload annotations without losing union or collection types."""
    flattened = []
    for annotation in annotations:
        if annotation is inspect.Signature.empty:
            continue
        values = typing.get_args(annotation) if typing.get_origin(annotation) in (typing.Union, types.UnionType) else (annotation,)
        for value in values:
            if value not in flattened:
                flattened.append(value)
    if not flattened:
        return inspect.Signature.empty
    if len(flattened) == 1:
        return flattened[0]
    return typing.Union[tuple(flattened)]


def _hint_namespace(target):
    """Build a fallback namespace for annotations whose names live elsewhere.

    Overloads often reference sibling classes (e.g. shape_core's Shape.__add__
    returns 'Self | Compound' while Compound lives in topology.composite), so
    module globals alone raise NameError inside typing.get_type_hints.
    """
    names = dict(vars(typing))
    names.update(vars(b3d))
    module = inspect.getmodule(target)
    if module is not None:
        names.update(vars(module))
    return names


def _resolve_hints(target):
    """Resolve type hints, tolerating cross-module ForwardRef annotations."""
    try:
        return typing.get_type_hints(target)
    except Exception:
        pass
    try:
        return typing.get_type_hints(target, _hint_namespace(target))
    except Exception:
        return {}


def _method_signature(function):
    """Return an implementation signature enriched with its declared overload contract."""
    signature = inspect.signature(function)
    overloads = typing.get_overloads(function)
    if not overloads:
        hints = _resolve_hints(function)
        return signature, hints, function

    overload_data = []
    for overload in overloads:
        overload_signature = inspect.signature(overload)
        overload_hints = _resolve_hints(overload)
        overload_data.append((overload_signature, overload_hints))

    merged_parameters = []
    for parameter in signature.parameters.values():
        annotations = []
        for overload_signature, overload_hints in overload_data:
            overload_parameter = overload_signature.parameters.get(parameter.name)
            if overload_parameter is not None:
                annotations.append(overload_hints.get(parameter.name, overload_parameter.annotation))
        merged_annotation = _union_annotations(annotations)
        merged_parameters.append(
            parameter.replace(annotation=merged_annotation)
            if merged_annotation is not inspect.Signature.empty else parameter
        )
    return_annotations = [
        overload_hints.get("return", overload_signature.return_annotation)
        for overload_signature, overload_hints in overload_data
    ]
    merged_return = _union_annotations(return_annotations)
    merged_signature = signature.replace(
        parameters=merged_parameters,
        return_annotation=merged_return,
    )
    # False tells the evaluator to use the merged annotations on this signature,
    # rather than re-reading the implementation's less-specific annotations.
    return merged_signature, {}, False


def _resolve_self_annotation(annotation, owner):
    """Resolve typing.Self against the class that declares a discovered member."""
    if annotation is getattr(typing, "Self", None):
        return owner
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return typing.Union[tuple(_resolve_self_annotation(arg, owner) for arg in typing.get_args(annotation))]
    return annotation


def _annotation_metadata(annotation):
    """Return structured socket metadata for Python generic annotations."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    metadata = {}
    collection_origins = {
        list, set, frozenset,
        collections.abc.Sequence, collections.abc.MutableSequence,
        collections.abc.Iterable, collections.abc.Collection,
        collections.abc.Set, collections.abc.MutableSet,
    }
    mapping_origins = {
        dict, typing.Dict, collections.abc.Mapping, collections.abc.MutableMapping,
    }
    # A bare collection ABC (e.g. Pos's v: Iterable) is a structural contract
    # too: get_origin returns None for the unsubscripted class itself.
    if origin is None and annotation in collection_origins:
        origin = annotation
        args = args or (typing.Any,)
    if origin in collection_origins:
        item_annotation = args[0] if args else typing.Any
        item_metadata = _annotation_metadata(item_annotation) if item_annotation is not typing.Any else {}
        nested_structure = item_metadata.get("data_structure") in ("list", "tree")
        metadata["data_structure"] = (
            "set" if origin in (set, frozenset, collections.abc.Set, collections.abc.MutableSet)
            else "tree" if nested_structure else "list"
        )
        metadata["item_type"] = item_metadata.get("item_type", _type_name(item_annotation))
    elif origin is tuple:
        metadata["data_structure"] = "tuple"
        element_args = [arg for arg in args if arg is not Ellipsis]
        if element_args:
            types_found = sorted({_type_name(arg) for arg in element_args})
            metadata["item_type"] = types_found[0] if len(types_found) == 1 else "Any"
        else:
            metadata["item_type"] = "Any"
    if origin in mapping_origins:
        metadata["data_structure"] = "dict"
        metadata["key_type"] = _type_name(args[0]) if args else "String"
        metadata["value_type"] = _type_name(args[1]) if len(args) > 1 else "Any"
    if origin in (typing.Union, types.UnionType):
        # Tuple alternatives are structural too. In particular build123d's
        # RotationLike contract is Rotation | tuple[float, float, float].
        # Without this, a JSON rotation [x, y, z] is item-mapped into three
        # scalar calls, eventually passing an int where a wrapped geometry /
        # rotation object is expected.
        collection_args = [
            arg for arg in args
            if typing.get_origin(arg) in collection_origins or arg in collection_origins
            or typing.get_origin(arg) is tuple
        ]
        scalar_args = [arg for arg in args if arg is not type(None) and arg not in collection_args]
        variants = sorted({_type_name(arg) for arg in scalar_args})
        if len(variants) > 1:
            metadata["variants"] = variants
        # A union such as Shape | Iterable[Shape] accepts either one object or
        # a collection. Keep that collection intact for the native operator.
        if collection_args:
            metadata["data_access"] = "structure"
            item_types = {
                _type_name(typing.get_args(collection)[0])
                for collection in collection_args if typing.get_args(collection)
            }
            if len(item_types) == 1:
                metadata["item_type"] = item_types.pop()
    return metadata


def _safe_default(value):
    if value is inspect.Signature.empty:
        return None, None, True, False
    if isinstance(value, enum.Enum):
        return value.name, repr(value), False, True
    if value is None or isinstance(value, (bool, int, float, str)):
        try:
            json.dumps(value, allow_nan=False)
            return value, repr(value), False, True
        except (TypeError, ValueError, OverflowError):
            return None, repr(value), False, False
    if isinstance(value, (tuple, list, dict)):
        try:
            return json.loads(json.dumps(value, allow_nan=False)), repr(value), False, True
        except (TypeError, ValueError, OverflowError):
            pass
    if type(value).__name__.lower() == "vector" and all(hasattr(value, axis) for axis in ("X", "Y", "Z")):
        try:
            vector = [float(value.X), float(value.Y), float(value.Z)]
            json.dumps(vector, allow_nan=False)
            return vector, repr(value), False, True
        except (TypeError, ValueError, OverflowError):
            return None, repr(value), False, False
    return None, repr(value), False, False


def _json_clean(value):
    """Recursively remove non-finite numbers before crossing the JS JSON boundary."""
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, float):
        return value if __import__("math").isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return None


def _populate_editor_default(socket):
    """Give required scalar/structured sockets usable initial values."""
    if "default" in socket or not socket.get("required"):
        return
    socket_type = socket["type"]
    if socket.get("data_structure") in ("list", "tree", "tuple", "set"):
        socket["default"] = []
        socket["default_source"] = "editor"
        return
    if socket.get("data_structure") == "dict":
        socket["default"] = {}
        socket["default_source"] = "editor"
        return
    defaults = {
        "Number": 1.0, "Boolean": False, "String": "",
        "Code": "", "Vector2": [0.0, 0.0], "Vector3": [0.0, 0.0, 0.0],
        "List": [], "Tuple": [], "Set": [], "Dict": {},
    }
    if socket_type == "Enum" and socket.get("options"):
        socket["default"] = socket["options"][0]
    elif socket_type in defaults:
        socket["default"] = defaults[socket_type]
    if "default" in socket:
        socket["default_source"] = "editor"


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
    if inspect.isclass(obj):
        # Builder/context-manager lifecycles cannot be represented by a stateless node call.
        for owner in getattr(obj, "__mro__", (obj,)):
            members = vars(owner)
            if "__enter__" in members and "__exit__" in members:
                return "stateful context-manager class"
    try:
        _api_signature(obj)
    except (TypeError, ValueError) as exc:
        return "signature unavailable: " + str(exc)
    return None


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




# Python's operator protocol is discovered from class members, not from
# build123d-specific wrapper functions. This registry only supplies the
# language-level symbol and default palette semantics for recognized operators.
# Domain-specific presentation belongs in _NODE_METADATA_OVERRIDES below.
_PYTHON_OPERATOR_SPECS = {
    "__add__": {"symbol": "+", "codegen_symbol": "+", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__sub__": {"symbol": "−", "codegen_symbol": "-", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__mul__": {"symbol": "×", "codegen_symbol": "*", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__rmul__": {"symbol": "×", "codegen_symbol": "*", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__truediv__": {"symbol": "÷", "codegen_symbol": "/", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__floordiv__": {"symbol": "//", "codegen_symbol": "//", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__mod__": {"symbol": "%", "codegen_symbol": "%", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__pow__": {"symbol": "**", "codegen_symbol": "**", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__matmul__": {"symbol": "@", "codegen_symbol": "@", "family": "arithmetic", "palette_category": "Math", "palette_section": "Operators"},
    "__and__": {"symbol": "&", "codegen_symbol": "&", "family": "bitwise", "palette_category": "Logic", "palette_section": "Bitwise operators"},
    "__or__": {"symbol": "|", "codegen_symbol": "|", "family": "bitwise", "palette_category": "Logic", "palette_section": "Bitwise operators"},
    "__xor__": {"symbol": "^", "codegen_symbol": "^", "family": "bitwise", "palette_category": "Logic", "palette_section": "Bitwise operators"},
    "__lshift__": {"symbol": "<<", "codegen_symbol": "<<", "family": "bitwise", "palette_category": "Logic", "palette_section": "Bitwise operators"},
    "__rshift__": {"symbol": ">>", "codegen_symbol": ">>", "family": "bitwise", "palette_category": "Logic", "palette_section": "Bitwise operators"},
    "__neg__": {"symbol": "−", "codegen_symbol": "-", "family": "unary", "palette_category": "Math", "palette_section": "Unary operators"},
    "__pos__": {"symbol": "+", "codegen_symbol": "+", "family": "unary", "palette_category": "Math", "palette_section": "Unary operators"},
    "__invert__": {"symbol": "~", "codegen_symbol": "~", "family": "unary", "palette_category": "Logic", "palette_section": "Bitwise operators"},
}

# Native Shape algebra keeps its actual Python method identity and signature.
# These are presentation overrides only: they do not register nodes or callables.
for _shape_method, _shape_presentation in {
    "__add__": {"name": "Shape + (Union)", "category": "Boolean", "palette_category": "Boolean", "palette_section": "Boolean operations"},
    "__sub__": {"name": "Shape − (Difference)", "category": "Boolean", "palette_category": "Boolean", "palette_section": "Boolean operations"},
    "__and__": {"name": "Shape & (Intersection)", "category": "Boolean", "palette_category": "Boolean", "palette_section": "Boolean operations"},
}.items():
    _NODE_METADATA_OVERRIDES[
        b3d.Shape.__module__ + "." + b3d.Shape.__name__ + ".operator." + _shape_method
    ] = _shape_presentation


_FLOW_PALETTE = {
    "Math": ("Math", "Arithmetic & trigonometry"),
    "Logic": ("Logic", "Logic"),
    "Collections": ("Data", "Collections"),
    "Series": ("Data", "Series"),
    "Strings": ("Data", "Strings"),
    "Conversions": ("Data", "Conversions"),
    "Spatial": ("Spatial", "Spatial operations"),
    "Utilities": ("Math", "Numeric utilities"),
    "Constants": ("Math", "Constants"),
    "Values": ("Data", "Literal values"),
    "Boolean": ("Boolean", "Boolean operations"),
    "Preview": ("Inspect", "Preview styling"),
}


def _add_node(found, callables, node_id, name, category, function, inputs, output_type, description="", item_type=None):
    """Register an app-owned Flow primitive with explicit provenance and palette metadata."""
    if category not in _FLOW_PALETTE:
        raise ValueError("Unknown Flow primitive category: " + str(category))
    palette_category, palette_section = _FLOW_PALETTE[category]
    defaults = {"step": 1, "count": 10, "stop": 10, "index": 0, "start": 0, "default": None}
    found[node_id] = {
        "id": node_id,
        "source": "flow",
        "kind": "value" if not inputs else "operation",
        "name": name,
        "category": category,
        "palette_category": palette_category,
        "palette_section": palette_section,
        "description": description,
        "inputs": [
            {
                "name": key, "type": "Any" if socket_type in {"List", "Tuple", "Set", "Dict"} else socket_type, "annotation": socket_type,
                "required": socket_type not in {"Number", "Boolean", "String", "List", "Tuple", "Set", "Dict"} and key != "default",
                **({"data_structure": {"List": "list", "Tuple": "tuple", "Set": "set"}[socket_type],
                    "data_access": "structure", "item_type": "Any"} if socket_type in {"List", "Tuple", "Set"} else {}),
                **({"data_structure": "dict", "data_access": "structure", "key_type": "Any", "value_type": "Any"} if socket_type == "Dict" else {}),
                **({"data_access": "item"} if socket_type not in {"List", "Tuple", "Set", "Dict"} else {}),
                **({"default": defaults.get(key, {"Number": 0.0, "Boolean": False, "String": "", "List": [], "Tuple": [], "Set": [], "Dict": {}}[socket_type])}
                   if socket_type in {"Number", "Boolean", "String", "List", "Tuple", "Set", "Dict"} else
                   ({"default": None} if key == "default" else {})),
            }
            for key, socket_type in inputs
        ],
        "outputs": [{
            "name": "result",
            "type": "Any" if output_type in {"List", "Tuple", "Set", "Dict"} else output_type,
            "annotation": output_type,
            **({"data_structure": {"List": "list", "Tuple": "tuple", "Set": "set"}[output_type],
                "item_type": item_type or "Any"} if output_type in {"List", "Tuple", "Set"} else {}),
            **({"data_structure": "dict", "key_type": "Any", "value_type": "Any"} if output_type == "Dict" else {}),
        }],
    }
    callables[node_id] = function


def _collection_eval(expression, item=None, index=0, accumulator=None):
    import math
    import operator
    safe_builtins = {
        "abs": abs, "min": min, "max": max, "round": round,
        "int": int, "float": float, "str": str, "bool": bool,
        "len": len, "sum": sum, "pow": pow,
    }
    scope = {"__builtins__": safe_builtins, "math": math, "operator": operator,
             "x": item, "item": item, "index": index, "i": index, "acc": accumulator}
    return eval(compile(expression, "<collection expression>", "eval"), scope, scope)



def _xy_grid(x_count, y_count, spacing_x, spacing_y, origin_x, origin_y):
    nx, ny = max(1, min(200, int(x_count))), max(1, min(200, int(y_count)))
    return [[float(origin_x) + x * float(spacing_x), float(origin_y) + y * float(spacing_y)]
            for y in range(ny) for x in range(nx)]


def _polar_points(count, radius, center_x, center_y, start_angle, end_angle):
    import math
    count = max(1, min(2000, int(count)))
    start, end = math.radians(float(start_angle)), math.radians(float(end_angle))
    angles = [start] if count == 1 else [start + (end - start) * i / count for i in range(count)]
    return [[float(center_x) + float(radius) * math.cos(angle),
             float(center_y) + float(radius) * math.sin(angle)] for angle in angles]

def _hex_grid(columns, rows, spacing, origin_x, origin_y):
    """Generate staggered hexagonal XY sample points."""
    columns = max(1, min(200, int(columns)))
    rows = max(1, min(200, int(rows)))
    spacing = float(spacing)
    if spacing <= 0:
        raise ValueError("Hex grid spacing must be positive")
    row_step = spacing * (3.0 ** 0.5) / 2.0
    return [[float(origin_x) + (column + 0.5 * (row % 2)) * spacing,
             float(origin_y) + row * row_step]
            for row in range(rows) for column in range(columns)]


def _cull_nth(items, n, offset):
    n = int(n)
    if n < 1:
        raise ValueError("Cull interval must be at least 1")
    offset = int(offset) % n
    return [item for index, item in enumerate(items) if index % n != offset]


def _cross_reference(a, b):
    return [[left, right] for left in a for right in b]


def _transpose(items):
    rows = [list(row) for row in items]
    if not rows:
        return []
    width = min(len(row) for row in rows)
    return [[row[index] for row in rows] for index in range(width)]


def _voronoi_2d(points, width, height):
    """Build clipped 2D Voronoi cells as a compound of planar faces."""
    points = [(float(point[0]), float(point[1])) for point in points]
    if not points:
        raise ValueError("Voronoi needs at least one point")
    if len(points) > 500:
        raise ValueError("Voronoi is limited to 500 seed points")
    width, height = float(width), float(height)
    if width <= 0 or height <= 0:
        raise ValueError("Voronoi bounds must be positive")
    bounds = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    faces = []
    for px, py in points:
        polygon = list(bounds)
        for qx, qy in points:
            if (px, py) == (qx, qy):
                continue
            nx, ny = qx - px, qy - py
            limit = (qx * qx + qy * qy - px * px - py * py) / 2.0
            if not polygon:
                break
            clipped = []
            previous = polygon[-1]
            previous_value = nx * previous[0] + ny * previous[1] - limit
            for current in polygon:
                current_value = nx * current[0] + ny * current[1] - limit
                previous_inside, current_inside = previous_value <= 1e-9, current_value <= 1e-9
                if previous_inside != current_inside:
                    denominator = previous_value - current_value
                    t = previous_value / denominator if abs(denominator) > 1e-15 else 0.0
                    clipped.append((previous[0] + t * (current[0] - previous[0]),
                                    previous[1] + t * (current[1] - previous[1])))
                if current_inside:
                    clipped.append(current)
                previous, previous_value = current, current_value
            polygon = clipped
        if len(polygon) >= 3:
            wire = b3d.Wire.make_polygon([b3d.Vector(x, y, 0) for x, y in polygon], close=True)
            faces.append(b3d.Face(wire))
    return b3d.Compound(children=faces)


def _remap(value, source_min, source_max, target_min, target_max, clamp):
    low, high = float(source_min), float(source_max)
    if abs(high - low) < 1e-15:
        raise ValueError("Source range cannot have zero length")
    t = (float(value) - low) / (high - low)
    if clamp:
        t = max(0.0, min(1.0, t))
    return float(target_min) + t * (float(target_max) - float(target_min))


def _shift_list(items, offset, wrap):
    values = list(items)
    if not values:
        return []
    amount = int(offset)
    if wrap:
        amount %= len(values)
        return values[-amount:] + values[:-amount] if amount else values
    if amount >= 0:
        amount = min(amount, len(values))
        return [None] * amount + values[:len(values) - amount]
    amount = min(-amount, len(values))
    return values[amount:] + [None] * amount


def _preview_style(shape, face_color="#a6b6c8", edge_color="#342b24", vertex_color="#ffb347", opacity=1.0, show_edges=True, show_vertices=True):
    """Pass geometry through; preview generation consumes the style parameters."""
    return shape


def _register_flow_nodes(found, callables):
    import math
    import operator

    # Editable literal sources make primitive values available without an upstream node.
    for node_id, name, socket_type, default, fn in [
        ("core.value.number", "Number", "Number", 0.0, lambda value: float(value)),
        ("core.value.string", "String", "String", "", lambda value: str(value)),
        ("core.value.boolean", "Boolean", "Boolean", False, lambda value: bool(value)),
    ]:
        found[node_id] = {
            "id": node_id, "source": "flow", "kind": "value", "name": name, "category": "Values",
            "palette_category": "Data", "palette_section": "Literal values",
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
    _add_node(found, callables, "core.logic.and", "And", "Logic", lambda a, b: operator.and_(a, b),
              [("a", "Boolean"), ("b", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.or", "Or", "Logic", lambda a, b: operator.or_(a, b),
              [("a", "Boolean"), ("b", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.not", "Not", "Logic", lambda value: operator.not_(value),
              [("value", "Boolean")], "Boolean")
    _add_node(found, callables, "core.logic.select", "Select", "Logic", lambda condition, if_true, if_false: if_true if condition else if_false,
              [("condition", "Boolean"), ("if_true", "Any"), ("if_false", "Any")], "Any")

    # String construction and common operations.
    _add_node(found, callables, "core.string.join", "Join", "Strings", lambda a, b, separator: str(a) + separator + str(b),
              [("a", "String"), ("b", "String"), ("separator", "String")], "String")
    _add_node(found, callables, "core.string.format", "Format", "Strings", lambda template, value: template.format(value),
              [("template", "String"), ("value", "Any")], "String")
    _add_node(found, callables, "core.string.replace", "Replace", "Strings", lambda value, old, new: value.replace(old, new),
              [("value", "String"), ("old", "String"), ("new", "String")], "String")
    _add_node(found, callables, "core.string.length", "String Length", "Strings", lambda value: len(value),
              [("value", "String")], "Number")
    _add_node(found, callables, "core.string.upper", "Uppercase", "Strings", lambda value: value.upper(),
              [("value", "String")], "String")
    _add_node(found, callables, "core.string.lower", "Lowercase", "Strings", lambda value: value.lower(),
              [("value", "String")], "String")
    _add_node(found, callables, "core.convert.to_number", "To Number", "Conversions", lambda value: float(value),
              [("value", "Any")], "Number")
    _add_node(found, callables, "core.convert.to_string", "To String", "Conversions", lambda value: str(value),
              [("value", "Any")], "String")

    # Vector operations use build123d's native Vector type.
    _add_node(found, callables, "core.vector.add", "Vector Add", "Spatial", lambda a, b: a + b,
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.subtract", "Vector Subtract", "Spatial", lambda a, b: a - b,
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.scale", "Vector Scale", "Spatial",
              lambda vector_value, factor: vector_value * factor,
              [("vector_value", "Vector3"), ("factor", "Number")], "Vector3")
    _add_node(found, callables, "core.vector.dot", "Dot Product", "Spatial", lambda a, b: a.dot(b),
              [("a", "Vector3"), ("b", "Vector3")], "Number")
    _add_node(found, callables, "core.vector.cross", "Cross Product", "Spatial",
              lambda a, b: a.cross(b),
              [("a", "Vector3"), ("b", "Vector3")], "Vector3")
    _add_node(found, callables, "core.vector.length", "Vector Length", "Spatial",
              lambda value: value.length,
              [("value", "Vector3")], "Number")
    _add_node(found, callables, "core.vector.normalize", "Normalize", "Spatial",
              lambda value: value.normalized(),
              [("value", "Vector3")], "Vector3")

    # First-class collection construction and transformation nodes.
    _add_node(found, callables, "core.list.length", "List Length", "Collections",
              len, [("items", "List")], "Number")
    _add_node(found, callables, "core.list.get", "Get Item", "Collections",
              lambda items, index: items[index], [("items", "List"), ("index", "Number")], "Any")
    _add_node(found, callables, "core.list.set", "Set Item", "Collections",
              lambda items, index, value: (lambda result: (result.__setitem__(index, value), result)[1])(list(items)),
              [("items", "List"), ("index", "Number"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.append", "Append", "Collections",
              lambda items, value: list(items) + [value], [("items", "List"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.insert", "Insert", "Collections",
              lambda items, index, value: (lambda result: (result.insert(index, value), result)[1])(list(items)),
              [("items", "List"), ("index", "Number"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.remove", "Remove Value", "Collections",
              lambda items, value: (lambda result: (result.remove(value), result)[1])(list(items)),
              [("items", "List"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.pop", "Remove At", "Collections",
              lambda items, index: (lambda result: (result.pop(index), result)[1])(list(items)),
              [("items", "List"), ("index", "Number")], "List")
    _add_node(found, callables, "core.list.contains", "Contains", "Collections",
              lambda items, value: value in items, [("items", "List"), ("value", "Any")], "Boolean")
    _add_node(found, callables, "core.list.index", "Find Index", "Collections",
              lambda items, value: items.index(value), [("items", "List"), ("value", "Any")], "Number")
    _add_node(found, callables, "core.list.slice", "Slice", "Collections",
              lambda items, start, stop, step: list(items)[slice(start, stop, step)],
              [("items", "List"), ("start", "Number"), ("stop", "Number"), ("step", "Number")], "List")
    _add_node(found, callables, "core.list.reverse", "Reverse", "Collections",
              lambda items: list(reversed(items)), [("items", "List")], "List")
    _add_node(found, callables, "core.list.sort", "Sort", "Collections",
              lambda items, reverse: sorted(items, reverse=reverse),
              [("items", "List"), ("reverse", "Boolean")], "List")
    _add_node(found, callables, "core.list.unique", "Unique", "Collections",
              lambda items: list(dict.fromkeys(items)), [("items", "List")], "List")
    _add_node(found, callables, "core.list.flatten", "Flatten One Level", "Collections",
              lambda items: [value for group in items for value in group],
              [("items", "List")], "List")
    _add_node(found, callables, "core.list.zip", "Zip", "Collections",
              lambda a, b: [list(pair) for pair in zip(a, b)],
              [("a", "List"), ("b", "List")], "List")
    _add_node(found, callables, "core.list.enumerate", "Enumerate", "Collections",
              lambda items, start: [[index, value] for index, value in enumerate(items, int(start))],
              [("items", "List"), ("start", "Number")], "List")

    # Numeric series are explicit list-producing nodes; stop is exclusive like Python range.
    _add_node(found, callables, "core.series.range", "Range", "Series",
              lambda start, stop, step: list(range(int(start), int(stop), int(step))),
              [("start", "Number"), ("stop", "Number"), ("step", "Number")], "List",
              item_type="Number")
    _add_node(found, callables, "core.series.linspace", "Linspace", "Series",
              lambda start, stop, count: ([float(start)] if int(count) <= 1 else
                  [float(start) + (float(stop) - float(start)) * i / (int(count) - 1) for i in range(int(count))]),
              [("start", "Number"), ("stop", "Number"), ("count", "Number")], "List",
              item_type="Number")
    _add_node(found, callables, "core.series.arithmetic", "Arithmetic Series", "Series",
              lambda start, step, count: [float(start) + i * float(step) for i in range(int(count))],
              [("start", "Number"), ("step", "Number"), ("count", "Number")], "List",
              item_type="Number")
    _add_node(found, callables, "core.series.geometric", "Geometric Series", "Series",
              lambda start, ratio, count: [float(start) * float(ratio) ** i for i in range(int(count))],
              [("start", "Number"), ("ratio", "Number"), ("count", "Number")], "List",
              item_type="Number")

    _add_node(found, callables, "core.list.first", "First", "Collections",
              lambda items: items[0], [("items", "List")], "Any")
    _add_node(found, callables, "core.list.last", "Last", "Collections",
              lambda items: items[-1], [("items", "List")], "Any")
    _add_node(found, callables, "core.list.take", "Take", "Collections",
              lambda items, count: list(items)[:int(count)], [("items", "List"), ("count", "Number")], "List")
    _add_node(found, callables, "core.list.drop", "Drop", "Collections",
              lambda items, count: list(items)[int(count):], [("items", "List"), ("count", "Number")], "List")
    _add_node(found, callables, "core.list.concat", "Concatenate", "Collections",
              lambda a, b: list(a) + list(b), [("a", "List"), ("b", "List")], "List")
    _add_node(found, callables, "core.list.sum", "Sum", "Collections",
              sum, [("items", "List")], "Number")
    _add_node(found, callables, "core.list.minimum", "List Minimum", "Collections",
              min, [("items", "List")], "Any")
    _add_node(found, callables, "core.list.maximum", "List Maximum", "Collections",
              max, [("items", "List")], "Any")
    _add_node(found, callables, "core.list.any", "Any", "Collections",
              any, [("items", "List")], "Boolean")
    _add_node(found, callables, "core.list.all", "All", "Collections",
              all, [("items", "List")], "Boolean")
    _add_node(found, callables, "core.list.count", "Count Value", "Collections",
              lambda items, value: items.count(value), [("items", "List"), ("value", "Any")], "Number")
    _add_node(found, callables, "core.list.map", "Map", "Collections",
              lambda items, expression: [_collection_eval(expression, item, index) for index, item in enumerate(items)],
              [("items", "List"), ("expression", "Code")], "List",
              "Evaluate an expression for each item. Use x or item for the current value and index for its position.")
    _add_node(found, callables, "core.list.filter", "Filter", "Collections",
              lambda items, expression: [item for index, item in enumerate(items) if _collection_eval(expression, item, index)],
              [("items", "List"), ("expression", "Code")], "List",
              "Keep items for which the expression is true. Use x or item and index.")
    _add_node(found, callables, "core.list.reduce", "Reduce", "Collections",
              lambda items, initial, expression: __import__("functools").reduce(
                  lambda acc, item_index: _collection_eval(expression, item_index[1], item_index[0], acc),
                  enumerate(items), initial),
              [("items", "List"), ("initial", "Any"), ("expression", "Code")], "Any",
              "Fold a sequence into one value. Use acc for the accumulated value, x for the current item, and index.")
    _add_node(found, callables, "core.convert.to_list", "To List", "Collections",
              lambda value: list(value), [("value", "Any")], "List")
    _add_node(found, callables, "core.convert.to_tuple", "To Tuple", "Collections",
              lambda value: tuple(value), [("value", "Any")], "Tuple")
    _add_node(found, callables, "core.convert.to_set", "To Set", "Collections",
              lambda value: set(value), [("value", "Any")], "Set")
    _add_node(found, callables, "core.convert.to_dict", "To Dictionary", "Collections",
              lambda value: dict(value), [("value", "Any")], "Dict")

    _add_node(found, callables, "core.string.split", "Split", "Strings",
              lambda value, separator: value.split(separator),
              [("value", "String"), ("separator", "String")], "List",
              item_type="String")
    _add_node(found, callables, "core.string.strip", "Strip", "Strings",
              lambda value: value.strip(), [("value", "String")], "String")
    _add_node(found, callables, "core.string.join_list", "Join List", "Strings",
              lambda items, separator: separator.join(str(item) for item in items),
              [("items", "List"), ("separator", "String")], "String")
    # Dictionary operations.
    _add_node(found, callables, "core.dict.construct", "Dictionary", "Collections",
              lambda keys, values: dict(zip(keys, values)),
              [("keys", "List"), ("values", "List")], "Dict")
    _add_node(found, callables, "core.dict.get", "Get Value", "Collections",
              lambda mapping, key, default: mapping.get(key, default),
              [("mapping", "Dict"), ("key", "Any"), ("default", "Any")], "Any")
    _add_node(found, callables, "core.dict.length", "Dictionary Length", "Collections",
              len, [("mapping", "Dict")], "Number")
    _add_node(found, callables, "core.dict.remove", "Remove Key", "Collections",
              lambda mapping, key: {k: v for k, v in mapping.items() if k != key},
              [("mapping", "Dict"), ("key", "Any")], "Dict")
    _add_node(found, callables, "core.dict.set", "Set Value", "Collections",
              lambda mapping, key, value: {**mapping, key: value},
              [("mapping", "Dict"), ("key", "Any"), ("value", "Any")], "Dict")
    _add_node(found, callables, "core.dict.keys", "Keys", "Collections",
              lambda mapping: list(mapping.keys()), [("mapping", "Dict")], "List")
    _add_node(found, callables, "core.dict.values", "Values", "Collections",
              lambda mapping: list(mapping.values()), [("mapping", "Dict")], "List")
    _add_node(found, callables, "core.dict.items", "Items", "Collections",
              lambda mapping: [[key, value] for key, value in mapping.items()], [("mapping", "Dict")], "List")
    _add_node(found, callables, "core.dict.merge", "Merge", "Collections",
              lambda a, b: {**a, **b}, [("a", "Dict"), ("b", "Dict")], "Dict")
    _add_node(found, callables, "core.dict.contains", "Has Key", "Collections",
              lambda mapping, key: key in mapping, [("mapping", "Dict"), ("key", "Any")], "Boolean")

    # Tuple and set conversions/operations.
    _add_node(found, callables, "core.tuple.get", "Tuple Item", "Collections",
              lambda items, index: items[index], [("items", "Tuple"), ("index", "Number")], "Any")
    _add_node(found, callables, "core.tuple.length", "Tuple Length", "Collections",
              len, [("items", "Tuple")], "Number")
    _add_node(found, callables, "core.tuple.slice", "Tuple Slice", "Collections",
              lambda items, start, stop: tuple(items[slice(start, stop)]),
              [("items", "Tuple"), ("start", "Number"), ("stop", "Number")], "Tuple")
    _add_node(found, callables, "core.tuple.concat", "Tuple Concatenate", "Collections",
              lambda a, b: tuple(a) + tuple(b), [("a", "Tuple"), ("b", "Tuple")], "Tuple")
    _add_node(found, callables, "core.tuple.to_list", "Tuple to List", "Collections",
              list, [("items", "Tuple")], "List")
    _add_node(found, callables, "core.set.union", "Set Union", "Collections",
              lambda a, b: set(a) | set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.intersection", "Set Intersection", "Collections",
              lambda a, b: set(a) & set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.difference", "Set Difference", "Collections",              lambda a, b: set(a) - set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.symmetric_difference", "Symmetric Difference", "Collections",
              lambda a, b: set(a) ^ set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.add", "Set Add", "Collections",
              lambda items, value: set(items) | {value}, [("items", "Set"), ("value", "Any")], "Set")
    _add_node(found, callables, "core.set.discard", "Set Remove", "Collections",
              lambda items, value: set(items) - {value}, [("items", "Set"), ("value", "Any")], "Set")
    _add_node(found, callables, "core.set.contains", "Set Contains", "Collections",
              lambda items, value: value in items, [("items", "Set"), ("value", "Any")], "Boolean")
    _add_node(found, callables, "core.set.length", "Set Length", "Collections",
              len, [("items", "Set")], "Number")
    _add_node(found, callables, "core.set.to_list", "Set to List", "Collections",
              lambda items: sorted(items, key=repr), [("items", "Set")], "List")


    # Spatial construction and Grasshopper-style data utilities.
    _add_node(found, callables, "core.spatial.xy_grid", "XY Grid", "Spatial", _xy_grid,
              [("x_count", "Number"), ("y_count", "Number"), ("spacing_x", "Number"),
               ("spacing_y", "Number"), ("origin_x", "Number"), ("origin_y", "Number")], "List",
              "Generate row-major XY points over a rectangular grid.")
    _add_node(found, callables, "core.spatial.polar_points", "Polar Points", "Spatial", _polar_points,
              [("count", "Number"), ("radius", "Number"), ("center_x", "Number"),
               ("center_y", "Number"), ("start_angle", "Number"), ("end_angle", "Number")], "List",
              "Distribute points around a circle; angles are in degrees.")
    _add_node(found, callables, "core.spatial.hex_grid", "Hex Grid Points", "Spatial", _hex_grid,
              [("columns", "Number"), ("rows", "Number"), ("spacing", "Number"),
               ("origin_x", "Number"), ("origin_y", "Number")], "List",
              "Generate staggered XY points on a regular hexagonal grid.")
    found["core.spatial.hex_grid"]["inputs"][2]["default"] = 10.0
    found["core.spatial.xy_grid"]["inputs"][2]["default"] = 10.0
    found["core.spatial.xy_grid"]["inputs"][3]["default"] = 10.0
    found["core.spatial.polar_points"]["inputs"][1]["default"] = 50.0
    found["core.spatial.polar_points"]["inputs"][5]["default"] = 360.0
    _add_node(found, callables, "core.spatial.voronoi_2d", "Voronoi 2D", "Spatial", _voronoi_2d,
              [("points", "List"), ("width", "Number"), ("height", "Number")], "Compound",
              "Create planar Voronoi cells clipped to a width × height rectangle.")
    found["core.spatial.voronoi_2d"]["kind"] = "geometry"
    found["core.spatial.voronoi_2d"]["inputs"][0]["default"] = [
        [18, 18], [55, 14], [91, 24], [34, 51], [76, 61], [15, 72], [105, 76]
    ]
    found["core.spatial.voronoi_2d"]["inputs"][1]["default"] = 120.0
    found["core.spatial.voronoi_2d"]["inputs"][2]["default"] = 90.0
    _add_node(found, callables, "core.spatial.distance_2d", "Distance 2D", "Spatial",
              lambda a, b: math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])),
              [("a", "List"), ("b", "List")], "Number",
              "Euclidean distance between two XY coordinate pairs.")
    _add_node(found, callables, "core.utility.remap", "Remap", "Utilities", _remap,
              [("value", "Number"), ("source_min", "Number"), ("source_max", "Number"),
               ("target_min", "Number"), ("target_max", "Number"), ("clamp", "Boolean")], "Number",
              "Map a value from one numeric interval to another.")
    _add_node(found, callables, "core.utility.interpolate", "Interpolate", "Utilities",
              lambda a, b, t: float(a) + (float(b) - float(a)) * float(t),
              [("a", "Number"), ("b", "Number"), ("t", "Number")], "Number",
              "Linear interpolation between two values.")
    found["core.utility.remap"]["inputs"][2]["default"] = 1.0
    found["core.utility.remap"]["inputs"][4]["default"] = 1.0
    _add_node(found, callables, "core.list.partition", "Partition List", "Collections",
              lambda items, size: [list(items[i:i + max(1, int(size))])
                                   for i in range(0, len(items), max(1, int(size)))],
              [("items", "List"), ("size", "Number")], "List",
              "Split a sequence into consecutive chunks.")
    _add_node(found, callables, "core.list.repeat", "Repeat Data", "Collections",
              lambda value, count: [value for _ in range(max(0, min(10000, int(count))))],
              [("value", "Any"), ("count", "Number")], "List")
    _add_node(found, callables, "core.list.cull_pattern", "Cull Pattern", "Collections",
              lambda items, pattern: [item for index, item in enumerate(items)
                                      if not pattern or not bool(pattern[index % len(pattern)])],
              [("items", "List"), ("pattern", "List")], "List",
              "Remove items wherever the repeating boolean pattern is true.")
    _add_node(found, callables, "core.list.shift", "Shift List", "Collections", _shift_list,
              [("items", "List"), ("offset", "Number"), ("wrap", "Boolean")], "List",
              "Shift sequence items left or right, optionally wrapping around.")
    _add_node(found, callables, "core.list.weave", "Weave", "Collections",
              lambda a, b: [item for pair in zip(a, b) for item in pair] + list(a[len(b):]) + list(b[len(a):]),
              [("a", "List"), ("b", "List")], "List",
              "Interleave two sequences while preserving remaining items.")
    _add_node(found, callables, "core.list.cull_nth", "Cull Every Nth", "Collections", _cull_nth,
              [("items", "List"), ("n", "Number"), ("offset", "Number")], "List",
              "Remove every Nth item, with a zero-based offset.")
    _add_node(found, callables, "core.list.cross_reference", "Cross Reference", "Collections", _cross_reference,
              [("a", "List"), ("b", "List")], "List",
              "Create the Cartesian product of two lists as pairs.")
    _add_node(found, callables, "core.list.transpose", "Transpose", "Collections", _transpose,
              [("items", "List")], "List",
              "Transpose rows; incomplete trailing columns are omitted.")
    _add_node(found, callables, "core.preview.style", "Preview Style", "Preview",
              _preview_style,
              [("shape", "Shape"), ("face_color", "String"), ("edge_color", "String"),
               ("vertex_color", "String"), ("opacity", "Number"),
               ("show_edges", "Boolean"), ("show_vertices", "Boolean")],
              "Shape", "Pass geometry through unchanged and customize its viewport appearance.")
    found["core.preview.style"]["kind"] = "geometry"
    for socket in found["core.preview.style"]["inputs"]:
        defaults = {"face_color": "#a6b6c8", "edge_color": "#342b24",
                    "vertex_color": "#ffb347", "opacity": 1.0,
                    "show_edges": True, "show_vertices": True}
        if socket["name"] in defaults:
            socket["default"] = defaults[socket["name"]]



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




def _register_python_nodes(found, callables):
    """Register the app's Python execution node; it is not a build123d API."""
    node_id = "core.python.function"
    found[node_id] = {
        "id": node_id,
        "source": "python",
        "kind": "operation",
        "name": "Python Function",
        "category": "Python",
        "palette_category": "Scripting",
        "palette_section": "Python",
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
    callables[node_id] = _python_function


def discover(include_full=True):
    global _CATALOG, _CATALOG_CALLABLES, _CATALOG_SIGNATURES, _CATALOG_HINT_TARGETS, _EVAL_CACHE, _PREVIEW_CACHE
    found = {}
    callables = {}
    signatures = {}
    hint_targets = {}
    shape_classes = set()
    api_classes = set()
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
                if getattr(obj, "__module__", "").split(".")[0] == "build123d":
                    api_classes.add(obj)
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
                overloads = typing.get_overloads(obj.__init__) if inspect.isclass(obj) else []
                constructor_signatures = []
                for overload in overloads:
                    overload_signature = inspect.signature(overload)
                    parameters = list(overload_signature.parameters.values())
                    if parameters and parameters[0].name in ("self", "cls"):
                        overload_signature = overload_signature.replace(parameters=parameters[1:])
                    constructor_signatures.append((overload_signature, overload))
                if not constructor_signatures:
                    constructor_signatures = [(_api_signature(obj), obj.__init__ if inspect.isclass(obj) else obj)]
            except (TypeError, ValueError, AttributeError):
                continue

            is_overloaded_constructor = inspect.isclass(obj) and len(constructor_signatures) > 1
            for overload_index, (signature, hint_target) in enumerate(constructor_signatures, start=1):
                inputs = []
                try:
                    hints = _resolve_hints(hint_target)
                except Exception:
                    hints = {}

                for parameter in signature.parameters.values():
                    annotation = hints.get(parameter.name, parameter.annotation)
                    if parameter.kind == parameter.VAR_POSITIONAL:
                        default, default_repr, required, has_default = [], "()", False, True
                        socket_type = "List"
                    elif parameter.kind == parameter.VAR_KEYWORD:
                        default, default_repr, required, has_default = {}, "{}", False, True
                        socket_type = "Dict"
                    else:
                        default, default_repr, required, has_default = _safe_default(parameter.default)
                        socket_type = _type_name(annotation)
                    socket = {
                        "name": parameter.name,
                        "kind": parameter.kind.name.lower(),
                        "type": socket_type,
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
                    if typing.get_origin(annotation) is typing.Literal:
                        socket["options"] = list(typing.get_args(annotation))
                    socket.update(_annotation_metadata(annotation))
                    socket.setdefault("data_access", "structure" if socket.get("data_structure") else "item")
                    _populate_editor_default(socket)
                    inputs.append(socket)

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

                overload_id = node_id + ".overload-" + str(overload_index) if is_overloaded_constructor else node_id
                display_name = name
                if is_overloaded_constructor:
                    display_name = name + "(" + ", ".join(parameter.name for parameter in signature.parameters.values()) + ")"

                module_parts = canonical_module.split(".")
                category = module_parts[-1].replace("_", " ").title() if len(module_parts) > 1 else "Build123d"
                doc = inspect.getdoc(obj) or ""
                if node_kind == "geometry":
                    palette_category, palette_section = "Create", "Geometry constructors"
                elif node_kind == "enum":
                    palette_category, palette_section = "Reference", "Enumerations"
                else:
                    palette_category, palette_section = "API", "Build123d API functions"

                found[overload_id] = {
                    "id": overload_id,
                    "source": "build123d",
                    "kind": node_kind,
                    "name": display_name,
                    "category": category,
                    "palette_category": palette_category,
                    "palette_section": palette_section,
                    "description": doc.split("\n")[0][:180],
                    "inputs": inputs,
                    "outputs": [{
                        "name": "result", "type": output_type, "annotation": "",
                        **_annotation_metadata(hints.get("return", signature.return_annotation)),
                        **({"variants": sorted({_type_name(arg) for arg in typing.get_args(hints.get("return", signature.return_annotation)) if arg is not type(None)})}
                           if typing.get_origin(hints.get("return", signature.return_annotation)) in (typing.Union, types.UnionType) else {}),
                    }],
                }
                callables[overload_id] = obj
                signatures[overload_id] = signature
                hint_targets[overload_id] = hint_target
                report["accepted"].append({"id": overload_id, "kind": node_kind})

    # Discover public instance methods and properties from the actual class
    # hierarchy. Introspection is static: descriptors are never evaluated here.
    # Only members declared by build123d classes are exposed; external OCP and
    # Python implementation details stay out of the graph catalog.
    # Python's operator protocol is part of the native API, even though its
    # methods are dunder members rather than public module-level functions.
    for cls in api_classes:
        # Member discovery is independent of constructor discovery. A class
        # such as ShapeList may have useful instance methods even when its
        # constructor cannot be represented as a graph node.
        seen_members = set()
        for owner in getattr(cls, "__mro__", (cls,)):
            if getattr(owner, "__module__", "").split(".")[0] != "build123d":
                continue
            for member_name, raw_member in vars(owner).items():
                operator_spec = _PYTHON_OPERATOR_SPECS.get(member_name)
                if (member_name.startswith("_") and operator_spec is None) or member_name in seen_members:
                    continue
                seen_members.add(member_name)
                node_prefix = owner.__module__ + "." + owner.__name__
                receiver_type = _type_name(owner)

                if isinstance(raw_member, property):
                    getter_fn = raw_member.fget
                    if getter_fn is None:
                        continue
                    try:
                        getter_signature = inspect.signature(getter_fn)
                    except (TypeError, ValueError):
                        getter_signature = None
                    if getter_signature is None:
                        continue
                    try:
                        getter_hints = _resolve_hints(getter_fn)
                    except Exception:
                        getter_hints = {}
                    return_annotation = getter_hints.get("return", getter_signature.return_annotation)
                    property_id = node_prefix + ".property." + member_name
                    if property_id in found:
                        continue
                    getter = lambda self, name=member_name: getattr(self, name)
                    property_output = _type_name(return_annotation)
                    property_socket = {
                        "name": "result", "type": property_output,
                        "annotation": str(return_annotation) if return_annotation is not inspect.Signature.empty else "",
                        **_annotation_metadata(return_annotation),
                    }
                    found[property_id] = {
                        "id": property_id, "source": "build123d",
                        "kind": "operation", "name": owner.__name__ + "." + member_name,
                        "category": owner.__name__ + " Properties",
                        "palette_category": "API", "palette_section": "Discovered properties",
                        "description": (inspect.getdoc(getter_fn) or "").split("\n")[0][:180],
                        "inputs": [{
                            "name": "self", "label": "object", "type": receiver_type,
                            "annotation": str(owner), "required": True, "data_access": "item",
                        }],
                        "outputs": [property_socket],
                    }
                    callables[property_id] = getter
                    continue

                if isinstance(raw_member, (staticmethod, classmethod)):
                    # Static/class methods are module/class API, not receiver members.
                    continue
                if not (inspect.isfunction(raw_member) or inspect.ismethoddescriptor(raw_member)
                        or inspect.isbuiltin(raw_member)):
                    continue
                try:
                    signature, hints, hint_target = _method_signature(raw_member)
                except (TypeError, ValueError):
                    continue
                parameters = list(signature.parameters.values())
                if not parameters or parameters[0].name not in ("self", "cls"):
                    continue
                inputs = [{
                    "name": parameters[0].name, "label": "object", "type": receiver_type,
                    "annotation": str(owner), "required": True, "data_access": "item",
                }]
                for parameter in parameters[1:]:
                    annotation = _resolve_self_annotation(
                        hints.get(parameter.name, parameter.annotation), owner
                    )
                    if parameter.kind == parameter.VAR_POSITIONAL:
                        default, default_repr, required, has_default = [], "()", False, True
                        socket_type = "List"
                    elif parameter.kind == parameter.VAR_KEYWORD:
                        default, default_repr, required, has_default = {}, "{}", False, True
                        socket_type = "Dict"
                    else:
                        default, default_repr, required, has_default = _safe_default(parameter.default)
                        socket_type = _type_name(annotation)
                    socket = {
                        "name": parameter.name, "kind": parameter.kind.name.lower(),
                        "type": socket_type,
                        "annotation": str(annotation) if annotation is not inspect.Signature.empty else "",
                        "required": required, "default_repr": default_repr,
                    }
                    if has_default:
                        socket["default"] = default
                    if inspect.isclass(annotation):
                        try:
                            if issubclass(annotation, enum.Enum):
                                socket["options"] = [member.name for member in annotation]
                        except TypeError:
                            pass
                    if typing.get_origin(annotation) is typing.Literal:
                        socket["options"] = list(typing.get_args(annotation))
                    socket.update(_annotation_metadata(annotation))
                    if parameter.kind == parameter.VAR_POSITIONAL:
                        socket.update({"data_structure": "list", "item_type": _type_name(annotation)})
                    elif parameter.kind == parameter.VAR_KEYWORD:
                        socket.update({"data_structure": "dict", "key_type": "String", "value_type": "Any"})
                    # A union such as Shape | Iterable[Shape] declares data_access
                    # without a data_structure; don't clobber that mixed contract.
                    socket.setdefault("data_access",
                                      "structure" if socket.get("data_structure") else "item")
                    _populate_editor_default(socket)
                    inputs.append(socket)

                if operator_spec:
                    category = owner.__name__ + " Operators"
                    palette_category = operator_spec["palette_category"]
                    palette_section = operator_spec["palette_section"]
                    method_id = node_prefix + ".operator." + member_name
                    display_name = owner.__name__ + " " + operator_spec["symbol"]
                else:
                    method_id = node_prefix + "." + member_name
                    category = owner.__name__ + " Methods"
                    palette_category, palette_section = "API", "Discovered methods"
                    display_name = owner.__name__ + "." + member_name
                if method_id in found:
                    continue
                return_annotation = hints.get("return", signature.return_annotation)
                resolved_return = _resolve_self_annotation(return_annotation, owner)
                output_socket = {
                    "name": "result", "type": _type_name(resolved_return),
                    "annotation": str(return_annotation) if return_annotation is not inspect.Signature.empty else "",
                    **_annotation_metadata(resolved_return),
                }
                found[method_id] = {
                    "id": method_id, "source": "build123d",
                    "kind": "operation", "name": display_name,
                    "category": category,
                    "palette_category": palette_category, "palette_section": palette_section,
                    "description": (inspect.getdoc(raw_member) or "").split("\n")[0][:180],
                    "inputs": inputs, "outputs": [output_socket],
                    **({"operator": {
                        "method": member_name,
                        "symbol": operator_spec["symbol"],
                        "family": operator_spec["family"],
                    }} if operator_spec else {}),
                }
                callables[method_id] = raw_member
                signatures[method_id] = signature
                hint_targets[method_id] = hint_target

            # Dataclass/typed fields are discoverable from annotations without
            # touching instances. Properties above take precedence.
            for field_name, field_annotation in vars(owner).get("__annotations__", {}).items():
                if field_name.startswith("_"):
                    continue
                raw_field = vars(owner).get(field_name)
                if isinstance(raw_field, property) or callable(raw_field):
                    continue
                field_id = owner.__module__ + "." + owner.__name__ + ".field." + field_name
                if field_id in found:
                    continue
                field_getter = lambda self, name=field_name: getattr(self, name)
                field_output = {
                    "name": "result", "type": _type_name(field_annotation),
                    "annotation": str(field_annotation),
                    **_annotation_metadata(field_annotation),
                }
                found[field_id] = {
                    "id": field_id, "source": "build123d",
                    "kind": "operation", "name": field_name,
                    "category": owner.__name__ + " Properties",
                    "palette_category": "API", "palette_section": "Discovered properties",
                    "description": "Read the " + field_name.replace("_", " ") + " field",
                    "inputs": [{
                        "name": "self", "label": "object", "type": receiver_type,
                        "annotation": str(owner), "required": True,
                    }],
                    "outputs": [field_output],
                }
                callables[field_id] = field_getter

    # Register app-owned node providers after native API discovery.
    _register_flow_nodes(found, callables)
    _register_python_nodes(found, callables)
    for definition in found.values():
        definition.setdefault("data_matching", "longest")

    # Keep curated presentation/eligibility policy separate from introspection.
    for node_id, override in _NODE_METADATA_OVERRIDES.items():
        if node_id not in found:
            continue
        if override.get("hidden"):
            found.pop(node_id, None)
            callables.pop(node_id, None)
            signatures.pop(node_id, None)
            hint_targets.pop(node_id, None)
            continue
        for field in ("name", "category", "description", "palette_category", "palette_section"):
            if field in override:
                found[node_id][field] = override[field]

    # Validate declared metadata. Missing provenance or palette placement is a
    # registration error; it is never inferred from IDs, names, or modules.
    for definition in found.values():
        node_id = definition.get("id", "<unknown>")
        if definition.get("source") not in {"build123d", "python", "flow"}:
            raise RuntimeError("Node has missing or invalid source metadata: " + node_id)
        if not definition.get("palette_category") or not definition.get("palette_section"):
            raise RuntimeError("Node has incomplete palette metadata: " + node_id)

    report["palette_source_counts"] = {}
    report["palette_category_counts"] = {}
    for definition in found.values():
        source = definition["source"]
        category = definition["palette_category"]
        report["palette_source_counts"][source] = report["palette_source_counts"].get(source, 0) + 1
        report["palette_category_counts"][source + "/" + category] = report["palette_category_counts"].get(source + "/" + category, 0) + 1

    report["node_count"] = len(found)
    report["kind_counts"] = {}
    report["category_counts"] = {}
    report["input_type_counts"] = {}
    report["untyped_inputs"] = []
    for definition in found.values():
        report["kind_counts"][definition["kind"]] = report["kind_counts"].get(definition["kind"], 0) + 1
        report["category_counts"][definition["category"]] = report["category_counts"].get(definition["category"], 0) + 1
        for socket in definition["inputs"]:
            socket_type = socket["type"]
            report["input_type_counts"][socket_type] = report["input_type_counts"].get(socket_type, 0) + 1
            if socket_type == "Any" and not socket.get("annotation"):
                report["untyped_inputs"].append(definition["id"] + ":" + socket["name"])
    report["required_api_presence"] = {
        name: any(item["name"] == name for item in found.values())
        for name in ("Box", "Cylinder", "Rectangle", "Circle", "extrude", "revolve")
    }
    global _DISCOVERY_REPORT
    _DISCOVERY_REPORT = report
    # Catalog replacement can change a node callable or signature; discard old values.
    _EVAL_CACHE.clear()
    _PREVIEW_CACHE.clear()
    _CATALOG = found
    _CATALOG_CALLABLES = callables
    _CATALOG_SIGNATURES = signatures
    _CATALOG_HINT_TARGETS = hint_targets
    return list(found.values())


def discovery_report_json():
    """Return an auditable snapshot of the most recent API discovery pass."""
    return json.dumps(_json_clean(_DISCOVERY_REPORT), allow_nan=False, default=str)


def catalog_json():
    if not _CATALOG:
        discover(include_full=False)
    return json.dumps(_json_clean(list(_CATALOG.values())), allow_nan=False)


def discover_full():
    return json.dumps(_json_clean(discover(include_full=True)), allow_nan=False)


def _iter_shapes(value):
    if isinstance(value, b3d.Shape):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_shapes(item)


def _as_export_shape(value):
    shapes = list(_iter_shapes(value))
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else b3d.Compound(children=shapes)


def _point_xyz(point):
    return [float(point.X), float(point.Y), float(point.Z)]


def _mesh_preview(value, style=None):
    shapes = list(_iter_shapes(value))
    if not shapes:
        return None
    flat_vertices, flat_triangles, edge_segments, vertex_points = [], [], [], []
    try:
        for shape in shapes:
            vertices, triangles = shape.tessellate(0.15, 0.25)
            offset = len(flat_vertices) // 3
            for vertex in vertices:
                flat_vertices.extend(_point_xyz(vertex))
            flat_triangles.extend(offset + int(index) for triangle in triangles for index in triangle)
            for edge in shape.edges():
                try:
                    samples = [_point_xyz(point) for point in edge.positions(deflection=0.15)]
                    for first, second in zip(samples, samples[1:]):
                        edge_segments.extend(first)
                        edge_segments.extend(second)
                except Exception:
                    endpoints = [_point_xyz(vertex.center()) for vertex in edge.vertices()]
                    if len(endpoints) >= 2:
                        edge_segments.extend(endpoints[0])
                        edge_segments.extend(endpoints[-1])
            for vertex in shape.vertices():
                vertex_points.extend(_point_xyz(vertex.center()))
        preview = {"vertices": flat_vertices, "triangles": flat_triangles,
                   "edges": edge_segments, "points": vertex_points}
        if style:
            preview.update({
                "faceColor": style.get("face_color", "#a6b6c8"),
                "edgeColor": style.get("edge_color", "#342b24"),
                "vertexColor": style.get("vertex_color", "#ffb347"),
                "opacity": max(0.0, min(1.0, float(style.get("opacity", 1.0)))),
                "showEdges": bool(style.get("show_edges", True)),
                "showVertices": bool(style.get("show_vertices", True)),
            })
        return preview
    except Exception as exc:
        raise RuntimeError("Topology preview extraction failed: " + str(exc))

def _inspect_value(value, depth=0):
    value_type = type(value).__name__
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"type": value_type, "value": value}
    if isinstance(value, b3d.Shape):
        # OCP-backed shapes expose lazy properties; walking dir()/getattr() can
        # trigger expensive kernel work and freeze the browser UI.
        return {"type": value_type, "repr": "<geometry value>"}
    if depth >= 5:
        return {"type": value_type, "repr": repr(value)[:1000], "truncated": True}
    if isinstance(value, collections.abc.Mapping):
        items = {}
        entries = list(value.items())
        for key, item in entries[:80]:
            items[str(key)] = _inspect_value(item, depth + 1)
        return {"type": value_type, "items": items, "length": len(value), "truncated": len(entries) > 80}
    if isinstance(value, (list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.Set)):
        sequence = list(value)
        return {"type": value_type, "items": [_inspect_value(item, depth + 1) for item in sequence[:80]], "length": len(sequence), "truncated": len(sequence) > 80}
    attributes = {}
    try:
        names = [name for name in dir(value) if not name.startswith("_")]
        for name in names[:80]:
            try:
                item = getattr(value, name)
                if callable(item):
                    continue
                if (isinstance(item, (bool, int, float, str, list, tuple, set, frozenset,
                                      collections.abc.Mapping, collections.abc.Sequence,
                                      collections.abc.Set)) or item is None or
                        type(item).__name__ in {"Vector", "Plane", "Location", "Rotation", "Axis", "Color"}):
                    attributes[name] = _inspect_value(item, depth + 1)
            except Exception:
                continue
    except Exception:
        pass
    return {"type": value_type, "repr": repr(value)[:2000], "attributes": attributes}


def _socket_data_structure(socket):
    """Return the declared collection shape of a socket, never its runtime value shape."""
    structure = socket.get("data_structure", "item")
    if structure != "item":
        return structure
    return {
        "List": "list", "Tuple": "tuple", "Set": "set", "Dict": "dict",
    }.get(socket.get("type"), "item")


def _effective_output_structure(node_id, output_name, nodes, incoming, memo=None, visiting=None):
    """Resolve output collection shape through item-consuming connections.

    This mirrors the editor's graph type propagation. A node that consumes a
    stream item-wise produces a stream of its own output; structure-consuming
    inputs do not propagate collection shape.
    """
    memo = memo if memo is not None else {}
    visiting = visiting if visiting is not None else set()
    key = (node_id, output_name)
    if key in memo:
        return memo[key]
    if key in visiting:
        return "item"
    node = nodes.get(node_id)
    if node is None:
        return "item"
    definition = node.get("data", {}).get("definition", {})
    output = next((item for item in definition.get("outputs", [])
                   if item.get("name") == output_name), None)
    if output is None:
        return "item"
    declared = _socket_data_structure(output)
    if declared != "item":
        memo[key] = declared
        return declared

    visiting.add(key)
    inferred = "item"
    for input_name, (source_id, source_output) in incoming.get(node_id, {}).items():
        input_socket = next((item for item in definition.get("inputs", [])
                             if item.get("name") == input_name), None)
        if input_socket is None:
            continue
        access = input_socket.get("data_access")
        if access is None:
            access = "structure" if _socket_data_structure(input_socket) != "item" else "item"
        if access != "item":
            continue
        upstream = _effective_output_structure(
            source_id, source_output, nodes, incoming, memo, visiting
        )
        if upstream == "tree":
            inferred = "tree"
            break
        if upstream in ("list", "tuple"):
            inferred = "list"
    visiting.remove(key)
    memo[key] = inferred
    return inferred


def _stream_item_kind(value):
    """Classify a stream item for input validation without importing anything."""
    if value is None or isinstance(value, (bool, int, float)):
        return "scalar"
    if isinstance(value, str):
        return "scalar"
    if isinstance(value, b3d.Shape):
        return "geometry"
    if isinstance(value, (list, tuple, set, frozenset,
                          collections.abc.Mapping, collections.abc.Sequence, collections.abc.Set)):
        return "collection"
    return "object"


_GEOMETRY_INPUT_TYPES = {"Shape", "Solid", "Compound", "Face", "Edge", "Vertex", "Wire", "Shell", "Part", "Sketch"}
_SCALAR_INPUT_TYPES = {"Number", "Boolean", "String", "Code"}
_OBJECT_INPUT_TYPES = {
    "Vector2", "Vector3", "Rotation", "Plane", "Location", "Axis", "Color", "Matrix", "Enum",
}


def _validate_stream_items(definition, params, stream_shapes):
    """Reject stream items the declared input types cannot accept.

    List[Any] outputs connect anywhere in the editor, so a numeric stream can
    reach an item-access input declared as Location or Matrix. Catch that
    before native code runs, so users get the socket name instead of an
    opaque kernel error such as "'int' object has no attribute 'wrapped'".
    """
    for input_socket in definition.get("inputs", []):
        name = input_socket.get("name")
        if name not in stream_shapes:
            continue
        value = params.get(name)
        if not isinstance(value, (list, tuple)) or not value:
            continue
        # Variants carry the contract when the merged union collapsed the type
        # to Any; mirror the editor's variant-first compatibility logic.
        target_types = list(input_socket.get("variants") or [])
        if not target_types:
            target_types = [input_socket.get("type")]
        if any(target in ("Any", "List", "Tuple", "Set", "Dict") for target in target_types):
            continue
        geometry_target = any(target in _GEOMETRY_INPUT_TYPES for target in target_types)
        scalar_target = any(target in _SCALAR_INPUT_TYPES for target in target_types)
        object_target = any(target in _OBJECT_INPUT_TYPES for target in target_types)
        if not (geometry_target or scalar_target or object_target):
            continue
        kinds = {_stream_item_kind(item) for item in value}
        accepts_scalar = scalar_target
        accepts_geometry = geometry_target
        accepts_object = object_target or geometry_target
        if "scalar" in kinds and not accepts_scalar:
            raise TypeError(
                "input '{}' expects {}; the connected stream supplies scalar items. "
                "Use a matching value node or a vector/list location instead.".format(
                    name, " or ".join(sorted(set(target_types)))
                )
            )
        if ("geometry" in kinds and not accepts_geometry) or (
            "object" in kinds and not (accepts_object or accepts_geometry)
        ):
            raise TypeError(
                "input '{}' expects {}; the connected stream supplies incompatible items".format(
                    name, " or ".join(sorted(set(target_types)))
                )
            )


def _map_item_inputs(params, stream_shapes, invoke, matching="longest", path=(), depth=0, _validate=None):
    """Map only inputs explicitly identified as graph streams.

    Python lists and tuples are ordinary values unless graph metadata says that
    a connected output carries a stream. A list-valued Vector, tuple argument,
    or collection passed to a structure-consuming API therefore stays intact.
    """
    sequences = {}
    for name, shape in stream_shapes.items():
        value = params.get(name)
        if shape in ("list", "tuple", "tree") and isinstance(value, (list, tuple)):
            sequences[name] = list(value)
    if not sequences:
        if _validate is not None and not path:
            _validate()
        try:
            return invoke(params)
        except Exception as exc:
            location = "".join("[{}]".format(index) for index in path)
            raise RuntimeError("Data mapping at {}: {}".format(location or "<root>", exc)) from exc
    if any(not value for value in sequences.values()):
        return []
    names = list(sequences)
    if _validate is not None and not path:
        _validate()
    if matching == "cross":
        rows = list(__import__("itertools").product(*(sequences[name] for name in names)))
    else:
        lengths = [len(value) for value in sequences.values()]
        count = min(lengths) if matching == "shortest" else max(lengths)
        rows = [
            tuple(sequence[index if index < len(sequence) else len(sequence) - 1]
                  for sequence in sequences.values())
            for index in range(count)
        ]
    results = []
    for index, row in enumerate(rows):
        child = dict(params)
        child_shapes = dict(stream_shapes)
        for name, value in zip(names, row):
            child[name] = value
            if stream_shapes[name] == "list" or not isinstance(value, (list, tuple)):
                child_shapes.pop(name, None)
        results.append(_map_item_inputs(
            child, child_shapes, invoke, matching, path + (index,), depth + 1
        ))
    return results


def _cache_key(definition_id, params, upstream_keys):
    payload = json.dumps([definition_id, params, sorted(upstream_keys.items())],
                         sort_keys=True, separators=(",", ":"), default=repr, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cacheable(definition):
    node_id = definition.get("id", "")
    name = definition.get("name", "")
    category = definition.get("category", "")
    if node_id.startswith("core.python.") or category.endswith((" Methods", " Properties")):
        return False
    if name in {"BuildPart", "BuildSketch", "BuildLine", "Build123d", "BuildSheet"}:
        return False
    return True


def _trim_cache(cache):
    while len(cache) > _CACHE_LIMIT:
        cache.pop(next(iter(cache)))


def _graph_plan(nodes, edges):
    """Validate graph input ownership and return one deterministic execution plan."""
    incoming = {}
    outgoing = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for edge in edges:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source not in nodes or target not in nodes:
            continue
        target_handle = edge.get("targetHandle")
        target_inputs = incoming.setdefault(target, {})
        if target_handle in target_inputs:
            raise ValueError(
                "Input '{}.{}' has multiple connections".format(target, target_handle)
            )
        target_inputs[target_handle] = (source, edge.get("sourceHandle"))
        outgoing[source].append(target)
        indegree[target] += 1

    # Scope activation and context arguments participate in graph ordering even
    # though they are runtime control-flow dependencies rather than value sockets.
    def add_ordering_dependency(source, target):
        if source not in nodes or target not in nodes or source == target:
            return
        if target not in outgoing[source]:
            outgoing[source].append(target)
            indegree[target] += 1

    scope_bindings = {
        node.get("data", {}).get("scope_binding"): node_id
        for node_id, node in nodes.items()
        if node.get("data", {}).get("scope_binding")
    }
    for node_id, node in nodes.items():
        data = node.get("data", {})
        if data.get("scope_binding"):
            for source_id in data.get("scope_dependencies", []):
                add_ordering_dependency(str(source_id), node_id)
        for scope_id in data.get("execution_scope_path", []):
            binding_node = scope_bindings.get(scope_id)
            if binding_node:
                add_ordering_dependency(binding_node, node_id)

    # Stable topological ordering preserves source order while respecting both
    # explicit data edges and the implicit dependencies of Python execution scopes.
    import heapq
    insertion_order = {node_id: index for index, node_id in enumerate(nodes)}
    queue = [(insertion_order[node_id], node_id)
             for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(queue)
    order = []
    while queue:
        _, node_id = heapq.heappop(queue)
        order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(queue, (insertion_order[target], target))
    if len(order) != len(nodes):
        raise ValueError("Graph contains a cycle")
    return incoming, outgoing, order


def evaluate_graph_json(payload):
    global _LAST_SHAPE
    _LAST_SHAPE = None
    graph = json.loads(payload)
    nodes = {str(node["id"]): node for node in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    try:
        incoming, outgoing, order = _graph_plan(nodes, edges)
    except ValueError as exc:
        return json.dumps({
            "previews": {}, "errors": {"graph": str(exc)}, "order": [],
            "cache": {"hits": 0, "misses": 0},
        })

    values, previews, errors, node_keys = {}, {}, {}, {}
    evaluated_params = {}
    cache_hits = cache_misses = 0

    # Imported Python context managers are represented as graph-level execution
    # scopes. The graph remains ordinary dataflow; this stack supplies the dynamic
    # context required by build123d builders and Locations-style managers.
    scope_definitions = {
        item.get("id"): item for item in graph.get("execution_scopes", [])
    }
    active_scopes = []
    scope_values = {}
    scope_results = {}
    failed_scopes = set()

    def restore_scope_value(value):
        if isinstance(value, list):
            return [restore_scope_value(item) for item in value]
        if isinstance(value, dict):
            if "__flow_ref__" in value:
                source_id, output_name = value["__flow_ref__"]
                if source_id not in values:
                    raise ValueError("Context argument depends on unavailable node " + str(source_id))
                return values[source_id].get(output_name)
            if "__flow_enum__" in value:
                module_name, class_name, member_name = value["__flow_enum__"]
                enum_type = getattr(importlib.import_module(module_name), class_name)
                return getattr(enum_type, member_name)
            if "__flow_object__" in value:
                object_type = value["__flow_object__"]
                constructor = getattr(b3d, object_type)
                if "args" in value or "kwargs" in value:
                    args = restore_scope_value(value.get("args", []))
                    kwargs = restore_scope_value(value.get("kwargs", {}))
                    return constructor(*args, **kwargs)
                if object_type == "Plane":
                    return constructor(origin=value["origin"], x_dir=value["x_dir"], z_dir=value["z_dir"])
                if object_type == "Location":
                    return constructor(value["value"])
                return constructor(*value["value"])
            return {key: restore_scope_value(item) for key, item in value.items()}
        return value

    def close_scope(scope_id, exception_info=(None, None, None)):
        entry = active_scopes.pop()
        manager = entry["manager"]
        descriptor = scope_definitions[scope_id]
        try:
            manager.__exit__(*exception_info)
        finally:
            output_attr = descriptor.get("result_attribute")
            if output_attr:
                scope_results[scope_id] = getattr(manager, output_attr, None)
            elif descriptor.get("has_binding"):
                scope_results[scope_id] = entry.get("entered")
            else:
                scope_results[scope_id] = None

    def transition_scopes(target_path):
        common = 0
        while common < len(active_scopes) and common < len(target_path):
            if active_scopes[common]["id"] != target_path[common]:
                break
            common += 1
        while len(active_scopes) > common:
            close_scope(active_scopes[-1]["id"])
        for scope_id in target_path[common:]:
            descriptor = scope_definitions.get(scope_id)
            if descriptor is None:
                raise ValueError("Unknown Python execution scope: " + str(scope_id))
            module = importlib.import_module(descriptor["module"])
            manager_type = getattr(module, descriptor["name"])
            manager_args = restore_scope_value(descriptor.get("args", []))
            manager_kwargs = restore_scope_value(descriptor.get("kwargs", {}))
            manager = manager_type(*manager_args, **manager_kwargs)
            try:
                entered = manager.__enter__()
            except Exception as exc:
                exception_info = (type(exc), exc, exc.__traceback__)
                while active_scopes:
                    try:
                        close_scope(active_scopes[-1]["id"], exception_info)
                    except Exception:
                        # Continue unwinding outer managers even if one cleanup fails.
                        pass
                raise
            scope_values[scope_id] = entered
            active_scopes.append({"id": scope_id, "manager": manager, "entered": entered})

    for node_id in order:
        node = nodes[node_id]
        data = node.get("data", {})
        scope_path = data.get("execution_scope_path", [])
        if any(scope_id in failed_scopes for scope_id in scope_path):
            errors[node_id] = "Execution skipped because an enclosing Python scope failed"
            continue
        try:
            transition_scopes(scope_path)
        except Exception as exc:
            errors[node_id] = "Execution scope: " + type(exc).__name__ + ": " + str(exc)
            failed_scopes.update(scope_path)
            continue

        scope_binding = data.get("scope_binding")
        if scope_binding:
            values[node_id] = {"result": scope_values.get(scope_binding)}
            continue
        scope_result = data.get("scope_result")
        if scope_result:
            if scope_result in failed_scopes:
                errors[node_id] = "Scope result unavailable because its body failed"
            else:
                values[node_id] = {"result": scope_results.get(scope_result)}
            continue

        definition_id = data.get("definition", {}).get("id")
        definition = _CATALOG.get(definition_id)
        if definition is None:
            errors[node_id] = "Unknown or unavailable API node: " + str(definition_id)
            continue
        params = dict(node.get("data", {}).get("params", {}))
        upstream_keys = {}
        failed_dependency = False
        upstream_cacheable = True
        for input_name, (source_id, output_name) in incoming.get(node_id, {}).items():
            if source_id in errors or source_id not in values:
                errors[node_id] = "Input depends on a failed node: " + source_id
                failed_dependency = True
                break
            params[input_name] = values[source_id].get(output_name)
            source_key = node_keys.get(source_id)
            if source_key is not None:
                upstream_keys[input_name] = source_key
            else:
                upstream_cacheable = False
        if failed_dependency:
            continue
        evaluated_params[node_id] = params

        # Builder operations have side effects on the active context and must
        # never be replayed from the stateless node cache.
        can_cache = not scope_path and _cacheable(definition) and upstream_cacheable
        try:
            key = _cache_key(
                definition_id, node.get("data", {}).get("params", {}), upstream_keys
            ) if can_cache else None
            cached = _EVAL_CACHE.get(key) if key else None
            if cached is not None:
                values[node_id] = {"result": cached}
                node_keys[node_id] = key
                cache_hits += 1
                _EVAL_CACHE.pop(key, None)
                _EVAL_CACHE[key] = cached
                continue

            cache_misses += 1
            callable_obj = _resolve_callable(definition_id)
            signature = _CATALOG_SIGNATURES.get(definition_id)
            hint_target = _CATALOG_HINT_TARGETS.get(definition_id)
            receiver_socket = next((item for item in definition.get("inputs", [])
                                    if item.get("name") in {"self", "cls"}), None)
            signature_parameters = list(signature.parameters.values()) if signature else []
            is_instance_method = (
                receiver_socket is not None
                and signature_parameters
                and signature_parameters[0].name == receiver_socket["name"]
                and ".operator." not in definition_id
            )
            if is_instance_method:
                receiver_name = receiver_socket["name"]
                method_name = definition_id.rsplit(".", 1)[-1]
                bound_signature = signature.replace(parameters=signature_parameters[1:])

                def invoke(mapped):
                    receiver_value = _coerce_graph_receiver(
                        mapped.get(receiver_name), receiver_socket
                    )
                    if receiver_value is None:
                        raise TypeError("Method {} requires receiver input '{}'; received inputs {}, incoming edges {}".format(
                            method_name, receiver_name, sorted(mapped),
                            sorted(incoming.get(node_id, {}))))
                    bound_method = getattr(receiver_value, method_name)
                    arguments = {name: value for name, value in mapped.items()
                                 if name != receiver_name}
                    return _call_with_coercion(
                        bound_method, arguments,
                        signature=bound_signature,
                        hint_target=hint_target,
                    )
            else:
                invoke = lambda mapped: _call_with_coercion(
                    callable_obj, mapped,
                    signature=signature,
                    hint_target=hint_target,
                )
            stream_shapes = {}
            shape_memo = {}
            for input_name, (source_id, source_output) in incoming.get(node_id, {}).items():
                input_socket = next((item for item in definition.get("inputs", [])
                                     if item.get("name") == input_name), None)
                if input_socket is None:
                    continue
                access = input_socket.get("data_access")
                if access is None:
                    access = "structure" if _socket_data_structure(input_socket) != "item" else "item"
                if access != "item":
                    continue
                source_shape = _effective_output_structure(
                    source_id, source_output, nodes, incoming, shape_memo
                )
                if source_shape in ("list", "tuple", "tree"):
                    stream_shapes[input_name] = "list" if source_shape == "tuple" else source_shape
            result = _map_item_inputs(
                params, stream_shapes, invoke,
                matching=definition.get("data_matching", "longest"),
                _validate=lambda: _validate_stream_items(definition, params, stream_shapes),
            )
            values[node_id] = {"result": result}
            if key:
                _EVAL_CACHE[key] = result
                _trim_cache(_EVAL_CACHE)
                node_keys[node_id] = key
        except Exception as exc:
            errors[node_id] = type(exc).__name__ + ": " + str(exc)
            # A failed operation aborts its active with-blocks and runs every
            # context manager's cleanup with the original exception information.
            if active_scopes:
                exception_info = (type(exc), exc, exc.__traceback__)
                failed_scopes.update(entry["id"] for entry in active_scopes)
                while active_scopes:
                    try:
                        close_scope(active_scopes[-1]["id"], exception_info)
                    except Exception as close_exc:
                        errors[node_id] += "; scope cleanup failed: " + str(close_exc)
                        continue

    while active_scopes:
        try:
            close_scope(active_scopes[-1]["id"])
        except Exception as exc:
            errors["scope_cleanup"] = type(exc).__name__ + ": " + str(exc)
            break

    source_nodes = {edge.get("source") for edge in edges}
    # Preview Style replaces the upstream viewport representation to avoid coplanar z-fighting.
    styled_sources = {
        edge.get("source") for edge in edges
        if nodes.get(edge.get("target"), {}).get("data", {}).get("definition", {}).get("id") == "core.preview.style"
        and nodes.get(edge.get("target"), {}).get("data", {}).get("visible") is not False
    }
    for node_id in order:
        if node_id not in values:
            continue
        visible = nodes[node_id].get("data", {}).get("visible")
        if node_id in styled_sources or visible is False or (visible is not True and node_id in source_nodes):
            continue
        result = values[node_id].get("result")
        key = node_keys.get(node_id)
        try:
            preview = _PREVIEW_CACHE.get(key) if key else None
            if preview is None:
                style = evaluated_params.get(node_id, {}) if nodes[node_id].get("data", {}).get("definition", {}).get("id") == "core.preview.style" else None
                preview = _mesh_preview(result, style)
                if preview is not None and key:
                    _PREVIEW_CACHE[key] = preview
                    _trim_cache(_PREVIEW_CACHE)
            elif key:
                _PREVIEW_CACHE.pop(key, None)
                _PREVIEW_CACHE[key] = preview
            if preview is not None:
                previews[node_id] = preview
                _LAST_SHAPE = _as_export_shape(result)
        except Exception as exc:
            errors[node_id] = "Preview: " + str(exc)

    return json.dumps(_json_clean({
        "previews": previews, "errors": errors, "order": order,
        "cache": {"hits": cache_hits, "misses": cache_misses},
        "socket_values": {
            node_id: {name: _inspect_value(value) for name, value in outputs.items()}
            for node_id, outputs in values.items()
            if not nodes[node_id].get("data", {}).get("scope_binding")
        },
    }), allow_nan=False)



def _coerce_annotation(value, annotation):
    """Convert JSON-shaped values into the Python collection and geometry types a method declares."""
    if annotation is None or annotation is inspect.Signature.empty or annotation is typing.Any:
        return value
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        candidates = [candidate for candidate in args if candidate is not type(None)]
        # Build123d RotationLike is Rotation | tuple[float, float, float].
        if isinstance(value, (list, tuple)) and any(
            inspect.isclass(candidate) and candidate.__name__ == "Rotation" for candidate in candidates
        ) and any(typing.get_origin(candidate) is tuple for candidate in candidates):
            return b3d.Rotation(tuple(value))
        for candidate in candidates:
            try:
                converted = _coerce_annotation(value, candidate)
                if converted is not value or isinstance(value, candidate):
                    return converted
            except Exception:
                continue
        return value
    if origin in (list, collections.abc.Sequence, collections.abc.MutableSequence,
                  collections.abc.Iterable, collections.abc.Collection):
        item_type = args[0] if args else typing.Any
        return [_coerce_annotation(item, item_type) for item in value]
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce_annotation(item, args[0]) for item in value)
        return tuple(_coerce_annotation(item, args[index] if index < len(args) else typing.Any)
                     for index, item in enumerate(value))
    if origin in (set, frozenset, collections.abc.Set, collections.abc.MutableSet):
        item_type = args[0] if args else typing.Any
        items = (_coerce_annotation(item, item_type) for item in value)
        return frozenset(items) if origin is frozenset else set(items)
    if origin in (dict, typing.Dict, collections.abc.Mapping, collections.abc.MutableMapping):
        key_type, value_type = args if len(args) > 1 else (typing.Any, typing.Any)
        return {_coerce_annotation(key, key_type): _coerce_annotation(item, value_type)
                for key, item in value.items()}
    if origin is typing.Literal:
        if value in args:
            return value
        raise ValueError("Expected one of: " + ", ".join(map(str, args)))
    if inspect.isclass(annotation):
        try:
            if issubclass(annotation, enum.Enum):
                return value if isinstance(value, annotation) else annotation[value] if isinstance(value, str) else annotation(value)
        except (TypeError, KeyError, ValueError):
            pass
        name = annotation.__name__.lower()
        if name == "vector" and isinstance(value, (list, tuple)):
            return b3d.Vector(*value)
        if name == "rotation" and isinstance(value, (list, tuple)):
            return b3d.Rotation(tuple(value))
        if name == "plane" and isinstance(value, dict):
            return b3d.Plane(**value)
        if name == "location" and isinstance(value, dict):
            return b3d.Location(**value)
        if name == "location" and isinstance(value, (list, tuple)):
            return b3d.Location(*value)
        if name == "axis" and isinstance(value, (list, tuple)):
            return b3d.Axis(*value)
        if name == "color" and isinstance(value, (list, tuple)):
            return b3d.Color(*value)
        if name == "matrix" and isinstance(value, (list, tuple)):
            # A 4×3 affine row list is the JSON shape of a Matrix.
            return b3d.Matrix(value if any(isinstance(row, (list, tuple)) for row in value) else [list(value)])
        if annotation is bool:
            return bool(value)
        if annotation is int:
            return int(value)
        if annotation is float:
            return float(value)
        if annotation is str:
            return str(value)
    return value


def _coerce_graph_receiver(value, socket):
    """Restore JSON-serialized build123d value objects before native method lookup."""
    value_types = {
        "Vector2": b3d.Vector,
        "Vector3": b3d.Vector,
        "Rotation": b3d.Rotation,
        "Plane": b3d.Plane,
        "Location": b3d.Location,
        "Axis": b3d.Axis,
        "Color": b3d.Color,
        "Matrix": b3d.Matrix,
    }
    annotation = value_types.get(socket.get("type"))
    return _coerce_annotation(value, annotation) if annotation is not None else value


def _call_with_coercion(callable_obj, params, signature=None, hint_target=None):
    if hint_target is False:
        target, hints = callable_obj, {}
    else:
        target = hint_target or (callable_obj.__init__ if inspect.isclass(callable_obj) else callable_obj)
        hints = _resolve_hints(target)
    if signature is None:
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
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, getattr(target, "__globals__", {}))
            except Exception:
                pass
        if annotation is not None and annotation is not inspect.Signature.empty:
            converted[name] = _coerce_annotation(value, annotation)

    positional = []
    keywords = {}
    positional_only = [
        (name, parameter) for name, parameter in signature.parameters.items()
        if parameter.kind == parameter.POSITIONAL_ONLY
    ]
    supplied = [i for i, (name, _) in enumerate(positional_only) if name in converted]
    last_supplied = max(supplied, default=-1)
    for index, (name, parameter) in enumerate(positional_only):
        if index > last_supplied:
            break
        if name in converted:
            positional.append(converted[name])
        elif parameter.default is not inspect.Signature.empty:
            positional.append(parameter.default)
        else:
            raise TypeError("Missing required positional argument: " + name)

    for name, parameter in signature.parameters.items():
        if parameter.kind == parameter.POSITIONAL_ONLY:
            continue
        if name not in converted:
            continue
        value = converted[name]
        if parameter.kind == parameter.VAR_POSITIONAL:
            positional.extend(value if isinstance(value, (list, tuple)) else [value])
        elif parameter.kind == parameter.VAR_KEYWORD:
            if isinstance(value, dict):
                keywords.update(value)
        else:
            keywords[name] = value
    return callable_obj(*positional, **keywords)

def _python_getattr(value, name):
    """Apply Python's native attribute lookup to a runtime graph value."""
    return getattr(value, name)


def _resolve_callable(path):
    if path in _CATALOG_CALLABLES:
        return _CATALOG_CALLABLES[path]
    module_name, attr_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _python_code_literal(value, annotation=None):
    """Render a JSON graph value as Python, preserving nested build123d enums."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        candidates = [item for item in args if item is not type(None)]
        selected = None
        for candidate in candidates:
            candidate_origin = typing.get_origin(candidate)
            try:
                if inspect.isclass(candidate) and issubclass(candidate, enum.Enum) and isinstance(value, str):
                    selected = candidate
                    break
            except TypeError:
                pass
            if candidate_origin in (list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.Iterable, collections.abc.Collection) and isinstance(value, (list, tuple, set)):
                selected = candidate
                break
        annotation = selected or (candidates[0] if candidates else None)
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
    if annotation is not None:
        try:
            if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
                member = annotation[value] if isinstance(value, str) else annotation(value)
                return "b3d.{}.{}".format(annotation.__name__, member.name)
        except (TypeError, KeyError, ValueError):
            pass
    if isinstance(value, enum.Enum):
        return "b3d.{}.{}".format(type(value).__name__, value.name)
    if isinstance(value, list):
        item_type = args[0] if args else None
        return "[" + ", ".join(_python_code_literal(item, item_type) for item in value) + "]"
    if isinstance(value, tuple):
        parts = []
        for index, item in enumerate(value):
            item_type = args[index] if origin is tuple and index < len(args) and args[-1:] != (Ellipsis,) else (
                args[0] if args else None
            )
            parts.append(_python_code_literal(item, item_type))
        return "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
    if isinstance(value, set):
        item_type = args[0] if args else None
        return "{" + ", ".join(_python_code_literal(item, item_type) for item in value) + "}"
    if isinstance(value, dict):
        key_type, value_type = args if len(args) > 1 else (None, None)
        return "{" + ", ".join(
            _python_code_literal(key, key_type) + ": " + _python_code_literal(item, value_type)
            for key, item in value.items()
        ) + "}"
    return repr(value)

def _python_node_annotation(definition, socket):
    node_id = definition["id"]
    target = _CATALOG_HINT_TARGETS.get(node_id)
    if target is False:
        target = None
    if target is None:
        callable_obj = _CATALOG_CALLABLES.get(node_id)
        target = callable_obj.__init__ if inspect.isclass(callable_obj) else callable_obj
    try:
        hints = _resolve_hints(target) if target is not None else {}
    except Exception:
        hints = {}
    annotation = hints.get(socket["name"])
    if annotation is not None:
        return annotation
    signature = _CATALOG_SIGNATURES.get(node_id)
    parameter = signature.parameters.get(socket["name"]) if signature else None
    annotation = parameter.annotation if parameter else None
    if isinstance(annotation, str) and target is not None:
        try:
            annotation = eval(annotation, getattr(target, "__globals__", {}))
        except Exception:
            pass
    return annotation


def _python_import_reference(obj, imports, alias):
    name = getattr(obj, "__name__", None)
    if name and getattr(b3d, name, None) is obj:
        return "b3d." + name
    module_name = getattr(obj, "__module__", None)
    if not name or not module_name:
        raise ValueError("Cannot generate a stable import for " + repr(obj))
    imports.append("from {} import {} as {}".format(module_name, name, alias))
    return alias



def import_python_code(source):
    """Parse a conservative, non-executing Python subset into the native graph format.

    Imports are inspected, never executed. Unsupported statements fail with a source
    location instead of being silently dropped or evaluated.
    """
    import ast
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Python source is empty")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("Python syntax error at line {}:{}: {}".format(
            exc.lineno or 1, exc.offset or 1, exc.msg
        )) from exc

    aliases = {"build123d": "b3d"}
    imported = {}
    viewer_modules = {}
    viewer_functions = set()
    viewer_api = {"show", "show_object", "show_all", "reset_show", "set_defaults"}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                if item.name == "build123d":
                    aliases[item.asname or item.name] = "b3d"
                elif item.name == "ocp_vscode":
                    viewer_modules[item.asname or item.name] = "ocp_vscode"
                else:
                    raise ValueError("Unsupported import at line {}: {}".format(
                        statement.lineno, item.name))
        elif isinstance(statement, ast.ImportFrom):
            if statement.module == "build123d" or (statement.module and statement.module.startswith("build123d.")):
                for item in statement.names:
                    if item.name != "*":
                        imported[item.asname or item.name] = item.name
            elif statement.module == "ocp_vscode":
                for item in statement.names:
                    if item.name == "*":
                        viewer_functions.update(viewer_api)
                    elif item.name in viewer_api:
                        viewer_functions.add(item.asname or item.name)
                    else:
                        raise ValueError("Unsupported ocp_vscode import at line {}: {}".format(
                            statement.lineno, item.name))
            else:
                raise ValueError("Only build123d and ocp_vscode imports are supported (line {})".format(statement.lineno))

    def is_viewer_call(node):
        if not isinstance(node, ast.Call):
            return False
        function = node.func
        if isinstance(function, ast.Name):
            return function.id in viewer_functions
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            return viewer_modules.get(function.value.id) == "ocp_vscode" and function.attr in viewer_api
        return False

    # Import is an explicit action, so load the full API catalog lazily here.
    # This makes class methods/properties available without slowing normal startup.
    if not _DISCOVERY_REPORT.get("include_full"):
        discover(include_full=True)
    definitions = _CATALOG
    def resolve_member(member_name, receiver_type, property_only=False):
        """Resolve one catalog method/property using its receiver socket contract."""
        candidates = []
        for candidate_id, candidate in definitions.items():
            is_property = ".property." + member_name in candidate_id or ".field." + member_name in candidate_id
            if property_only:
                if not is_property:
                    continue
            elif candidate_id.rsplit(".", 1)[-1] != member_name:
                continue
            receiver = next((item for item in candidate.get("inputs", [])
                             if item.get("name") in {"self", "cls"}), None)
            if receiver is None:
                continue
            accepted = receiver.get("variants") or [receiver.get("type", "Any")]
            if receiver_type in accepted:
                score = 3
            else:
                actual_class = getattr(b3d, receiver_type, None) if isinstance(receiver_type, str) else None
                compatible = False
                if inspect.isclass(actual_class):
                    for accepted_type in accepted:
                        expected_class = getattr(b3d, accepted_type, None) if isinstance(accepted_type, str) else None
                        if inspect.isclass(expected_class):
                            try:
                                if issubclass(actual_class, expected_class):
                                    compatible = True
                                    break
                            except TypeError:
                                pass
                if compatible:
                    score = 2
                elif "Any" in accepted:
                    score = 1
                else:
                    continue
            candidates.append((score, candidate_id))
        # Type inference is necessarily incomplete for chained collection
        # expressions and generic return annotations. If no receiver-compatible
        # method was found, permit a uniquely named discovered method. The graph
        # evaluator binds it to the actual receiver at runtime, so Python's own
        # attribute lookup remains the final compatibility check. Multiple
        # implementations remain ambiguous and are rejected.
        if not candidates:
            for candidate_id, candidate in definitions.items():
                if candidate_id.rsplit(".", 1)[-1] != member_name:
                    continue
                if any(item.get("name") in {"self", "cls"}
                       for item in candidate.get("inputs", [])):
                    candidates.append((0, candidate_id))
        if not candidates:
            return None, ()
        best_score = max(score for score, _ in candidates)
        winners = tuple(sorted(
            candidate_id for score, candidate_id in candidates if score == best_score
        ))
        return (winners[0], winners) if len(winners) == 1 else (None, winners)

    # Python's subscription protocol is a language-level operation, not a
    # build123d API member. Register it as a reusable graph primitive so
    # indexing works for sequences, mappings, and geometry collections alike.
    _PYTHON_SUBSCRIPT_ID = "core.python.subscript"
    _PYTHON_SLICE_ID = "core.python.slice"
    _PYTHON_ATTRIBUTE_ID = "core.python.attribute"
    definitions[_PYTHON_SUBSCRIPT_ID] = {
        "id": _PYTHON_SUBSCRIPT_ID, "name": "Python Subscript",
        "source": "build123d", "kind": "function", "category": "Python",
        "inputs": [
            {"name": "value", "type": "Any", "required": True},
            {"name": "index", "type": "Any", "required": True},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    definitions[_PYTHON_SLICE_ID] = {
        "id": _PYTHON_SLICE_ID, "name": "Python Slice",
        "source": "build123d", "kind": "function", "category": "Python",
        "inputs": [
            {"name": "start", "type": "Any", "required": False, "default": None},
            {"name": "stop", "type": "Any", "required": False, "default": None},
            {"name": "step", "type": "Any", "required": False, "default": None},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    definitions[_PYTHON_ATTRIBUTE_ID] = {
        "id": _PYTHON_ATTRIBUTE_ID, "name": "Python Attribute",
        "source": "build123d", "kind": "function", "category": "Python",
        "inputs": [
            {"name": "value", "type": "Any", "required": True},
            {"name": "name", "type": "String", "required": True},
        ],
        "outputs": [{"name": "result", "type": "Any"}],
    }
    _CATALOG_CALLABLES[_PYTHON_SUBSCRIPT_ID] = operator.getitem
    _CATALOG_CALLABLES[_PYTHON_SLICE_ID] = slice
    _CATALOG_CALLABLES[_PYTHON_ATTRIBUTE_ID] = _python_getattr
    by_name = {}
    for definition_id, definition in definitions.items():
        by_name.setdefault(definition.get("name"), definition_id)
        by_name.setdefault(definition_id.rsplit(".", 1)[-1], definition_id)

    graph_nodes, graph_edges = [], []
    variables = {}
    active_scope_ids = []
    execution_scopes = []
    scope_counter = 0
    counter = 0

    def add_node(definition_id, params, links, line):
        nonlocal counter
        definition = definitions.get(definition_id)
        if definition is None:
            raise ValueError("No registered node for '{}' at line {}".format(definition_id, line))
        counter += 1
        node_id = "import_{}".format(counter)
        builder_scope_active = any(
            next((scope.get("name") for scope in execution_scopes if scope.get("id") == scope_id), None)
            in {"BuildPart", "BuildSketch", "BuildLine"}
            for scope_id in active_scope_ids
        )
        graph_nodes.append({
            "id": node_id, "type": "api",
            "position": {"x": 80 + ((counter - 1) % 4) * 300, "y": 80 + ((counter - 1) // 4) * 190},
            "data": {"definition": definition, "params": params,
                     "execution_scope_path": list(active_scope_ids),
                     "visible": not builder_scope_active and any(
                socket.get("type") in {"Shape", "Solid", "Compound", "Part", "Sketch"}
                for socket in definition.get("outputs", [])
            )}
        })
        for input_name, source_id, output_name in links:
            graph_edges.append({
                "id": "import_edge_{}".format(len(graph_edges) + 1),
                "source": source_id, "target": node_id,
                "sourceHandle": output_name, "targetHandle": input_name
            })
        return node_id

    def resolve_callable(node):
        if isinstance(node, ast.Name):
            name = imported.get(node.id, node.id)
            return by_name.get(name) or by_name.get(node.id)
        if isinstance(node, ast.Attribute):
            path = []
            current = node
            while isinstance(current, ast.Attribute):
                path.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                root = current.id
                if root in aliases and aliases[root] == "b3d":
                    return by_name.get(path[0]) if len(path) == 1 else None
                if root in imported:
                    return by_name.get(imported[root] + "." + ".".join(reversed(path)))
            return None
        return None

    def literal_json_value(value):
        if isinstance(value, enum.Enum):
            return value.name
        if isinstance(value, b3d.Axis):
            return [list(value.position), list(value.direction)]
        if isinstance(value, b3d.Plane):
            return {"origin": list(value.origin), "x_dir": list(value.x_dir),
                    "z_dir": list(value.z_dir)}
        if isinstance(value, b3d.Location):
            return list(value.to_tuple())
        if isinstance(value, b3d.Vector):
            return list(value)
        if isinstance(value, dict):
            if "__flow_object__" in value:
                args = [literal_json_value(item) for item in value.get("args", [])]
                kwargs = {key: literal_json_value(item)
                          for key, item in value.get("kwargs", {}).items()}
                if kwargs and value["__flow_object__"] in {"Plane", "Location"}:
                    return kwargs
                return args if args else kwargs
            return {key: literal_json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [literal_json_value(item) for item in value]
        return value

    def materialize_literal(value):
        if isinstance(value, dict):
            if "__flow_object__" in value:
                constructor = getattr(b3d, value["__flow_object__"])
                args = [materialize_literal(item) for item in value.get("args", [])]
                kwargs = {key: materialize_literal(item)
                          for key, item in value.get("kwargs", {}).items()}
                return constructor(*args, **kwargs)
            return {key: materialize_literal(item) for key, item in value.items()}
        if isinstance(value, list):
            return [materialize_literal(item) for item in value]
        if isinstance(value, tuple):
            return tuple(materialize_literal(item) for item in value)
        return value

    def resolve_value_constructor(function_node, expected_type):
        """Resolve a build123d value class against the destination socket contract."""
        if isinstance(function_node, ast.Name):
            class_name = imported.get(function_node.id, function_node.id)
            constructor = getattr(b3d, class_name, None)
        elif isinstance(function_node, ast.Attribute):
            parts = []
            current = function_node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            constructor = None
            if isinstance(current, ast.Name) and current.id in aliases and aliases[current.id] == "b3d":
                path = list(reversed(parts))
                constructor = getattr(b3d, path[0], None) if len(path) == 1 else None
        else:
            return None
        if not inspect.isclass(constructor):
            return None
        if getattr(constructor, "__module__", "").split(".")[0] != "build123d":
            return None
        constructor_type = _type_name(constructor)
        if constructor_type not in _OBJECT_INPUT_TYPES:
            return None
        if expected_type not in (None, "Any", constructor_type):
            return None
        return constructor, constructor_type

    def expression(node, expected_type=None):
        if isinstance(node, ast.Constant):
            return ("literal", node.value)
        if isinstance(node, ast.Set):
            raise ValueError("Set literals are not yet importable (line {})".format(node.lineno))
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [expression(item) for item in node.elts]
            if all(item[0] == "literal" for item in values):
                raw = [item[1] for item in values]
                return ("literal", raw if isinstance(node, ast.List) else tuple(raw))
            raise ValueError("Connected collections are not yet importable (line {})".format(node.lineno))
        if isinstance(node, ast.Dict):
            keys = [expression(item) for item in node.keys]
            values = [expression(item) for item in node.values]
            if any(key[0] != "literal" for key in keys) or any(value[0] != "literal" for value in values):
                raise ValueError("Connected dictionaries are not yet importable (line {})".format(node.lineno))
            return ("literal", {key[1]: value[1] for key, value in zip(keys, values)})
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            if node.id in {"True", "False", "None"}:
                return ("literal", {"True": True, "False": False, "None": None}[node.id])
            raise ValueError("Unresolved name '{}' at line {}".format(node.id, node.lineno))
        if isinstance(node, ast.Attribute):
            receiver_root = node.value
            while isinstance(receiver_root, ast.Attribute):
                receiver_root = receiver_root.value
            receiver = None
            if ((isinstance(receiver_root, ast.Name) and receiver_root.id in variables)
                    or isinstance(node.value, (ast.Call, ast.Subscript, ast.BinOp, ast.UnaryOp))):
                receiver = expression(node.value)
            if receiver and receiver[0] == "literal":
                try:
                    return ("literal", getattr(materialize_literal(receiver[1]), node.attr))
                except AttributeError:
                    pass
            if receiver and receiver[0] == "ref":
                receiver_node = next((item for item in graph_nodes if item["id"] == receiver[1]), None)
                receiver_type = receiver_node["data"]["definition"].get("outputs", [{}])[0].get("type") if receiver_node else None
                if receiver_node and receiver_node.get("data", {}).get("scope_result"):
                    scope_id = receiver_node["data"]["scope_result"]
                    descriptor = next((item for item in execution_scopes if item.get("id") == scope_id), {})
                    if node.attr == descriptor.get("result_attribute"):
                        return receiver
                definition_id, candidates = resolve_member(
                    node.attr, receiver_type, property_only=True
                )
                if definition_id:
                    receiver_socket = next(
                        item for item in definitions[definition_id].get("inputs", [])
                        if item.get("name") in {"self", "cls"}
                    )
                    created = add_node(
                        definition_id, {},
                        [(receiver_socket["name"], receiver[1], receiver[2])],
                        node.lineno,
                    )
                    return ("ref", created, definitions[definition_id].get("outputs", [{}])[0].get("name", "result"))
                if candidates:
                    # When static receiver typing cannot distinguish same-named
                    # properties (for example Axis.Y, Vector.Y and Vertex.Y),
                    # preserve Python semantics and defer attribute lookup to
                    # the actual runtime object instead of guessing a class.
                    created = add_node(
                        _PYTHON_ATTRIBUTE_ID, {"name": node.attr},
                        [("value", receiver[1], receiver[2])], node.lineno,
                    )
                    return ("ref", created, "result")
            path = []
            current = node
            while isinstance(current, ast.Attribute):
                path.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                root = current.id
                if root in aliases and aliases[root] == "b3d":
                    path = list(reversed(path))
                    if path:
                        value = getattr(b3d, path[0], None)
                        for part in path[1:]:
                            value = getattr(value, part, None)
                        if isinstance(value, (enum.Enum, b3d.Axis, b3d.Plane, b3d.Location, b3d.Vector)):
                            return ("literal", value)
                elif hasattr(b3d, root):
                    value = getattr(b3d, root, None)
                    for part in reversed(path):
                        value = getattr(value, part, None)
                    if isinstance(value, (enum.Enum, b3d.Axis, b3d.Plane, b3d.Location, b3d.Vector)):
                        return ("literal", value)
            raise ValueError("Unsupported attribute expression at line {}".format(node.lineno))
        if isinstance(node, ast.Call):
            # Resolve build123d value constructors from the destination socket's
            # declared type. This shares the runtime's type contract instead of
            # maintaining one importer exception per value class.
            value_constructor = resolve_value_constructor(node.func, expected_type)
            if value_constructor is not None:
                constructor, constructor_type = value_constructor
                values = [expression(argument) for argument in node.args]
                kwargs = {}
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise ValueError("Expanded constructor arguments are unsupported at line {}".format(node.lineno))
                    kwargs[keyword.arg] = expression(keyword.value)
                if any(item[0] != "literal" for item in values) or any(item[0] != "literal" for item in kwargs.values()):
                    raise ValueError("{} requires literal constructor arguments (line {})".format(
                        constructor_type, node.lineno))
                if node.args and node.keywords and constructor_type in {"Plane", "Location"}:
                    raise ValueError("{} cannot mix positional and keyword arguments at line {}".format(
                        constructor_type, node.lineno))
                return ("literal", {
                    "__flow_object__": constructor.__name__,
                    "args": [item[1] for item in values],
                    "kwargs": {name: item[1] for name, item in kwargs.items()},
                })

            definition_id = resolve_callable(node.func)
            receiver = None
            receiver_type = None
            if not definition_id and isinstance(node.func, ast.Attribute):
                receiver = expression(node.func.value)
                if receiver[0] == "ref":
                    upstream = next((item for item in graph_nodes if item["id"] == receiver[1]), None)
                    if upstream:
                        receiver_type = upstream["data"]["definition"].get("outputs", [{}])[0].get("type")
                elif receiver[0] == "literal":
                    receiver_type = _type_name(type(materialize_literal(receiver[1])))
                definition_id, candidates = resolve_member(node.func.attr, receiver_type)
                if candidates and definition_id is None:
                        raise ValueError("Ambiguous method '{}' for receiver type '{}' at line {}: {}".format(
                            node.func.attr, receiver_type, node.lineno, ", ".join(candidates)))
            if not definition_id:
                raise ValueError("Unsupported or ambiguous call at line {}: {}".format(
                    node.lineno, ast.unparse(node.func)))
            definition = definitions[definition_id]
            sockets = definition.get("inputs", [])
            params, links = {}, []
            receiver_socket = next((item for item in sockets if item.get("name") in {"self", "cls"}), None)
            if receiver is not None and receiver_socket is not None:
                if receiver[0] == "ref":
                    links.append((receiver_socket["name"], receiver[1], receiver[2]))
                else:
                    params[receiver_socket["name"]] = literal_json_value(receiver[1])
            argument_sockets = [socket for socket in sockets if socket is not receiver_socket and socket.get("kind") != "var_keyword"]
            if len(node.args) > len(argument_sockets):
                raise ValueError("Too many positional arguments for {} at line {}".format(
                    definition.get("name"), node.lineno))
            supplied = {}
            for index, value in enumerate(node.args):
                supplied[argument_sockets[index]["name"]] = value
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise ValueError("Expanded keyword arguments are unsupported at line {}".format(node.lineno))
                supplied[keyword.arg] = keyword.value
            known = {socket["name"] for socket in sockets}
            unknown = set(supplied) - known
            if unknown:
                raise ValueError("Unknown argument(s) for {} at line {}: {}".format(
                    definition.get("name"), node.lineno, ", ".join(sorted(unknown))))
            for socket in sockets:
                name = socket["name"]
                if socket is receiver_socket:
                    continue
                if name not in supplied:
                    if "default" in socket:
                        params[name] = socket["default"]
                    elif socket.get("required"):
                        raise ValueError("Missing '{}' for {} at line {}".format(
                            name, definition.get("name"), node.lineno))
                    continue
                kind, value, *extra = expression(supplied[name], socket.get("type"))
                if kind == "literal":
                    params[name] = literal_json_value(value)
                else:
                    links.append((name, value, extra[0]))
            created = add_node(definition_id, params, links, node.lineno)
            return ("ref", created, definition.get("outputs", [{}])[0].get("name", "result"))
        if isinstance(node, ast.Subscript):
            # Preserve Python's native __getitem__ semantics. The receiver and
            # index are ordinary graph dependencies, so this also handles
            # geometry collections and values produced by earlier API calls.
            receiver = expression(node.value)
            index_node = node.slice
            if isinstance(index_node, ast.Slice):
                slice_params, slice_links = {}, []
                for socket_name, component in (
                    ("start", index_node.lower), ("stop", index_node.upper),
                    ("step", index_node.step),
                ):
                    if component is None:
                        slice_params[socket_name] = None
                        continue
                    kind, value, *extra = expression(component)
                    if kind == "literal":
                        slice_params[socket_name] = literal_json_value(value)
                    else:
                        slice_links.append((socket_name, value, extra[0]))
                index_node_id = add_node(_PYTHON_SLICE_ID, slice_params, slice_links, node.lineno)
                index = ("ref", index_node_id, "result")
            else:
                index = expression(index_node)
            if receiver[0] == "literal" and index[0] == "literal":
                try:
                    return ("literal", operator.getitem(
                        materialize_literal(receiver[1]),
                        materialize_literal(index[1]),
                    ))
                except (TypeError, KeyError, IndexError) as exc:
                    raise ValueError("Invalid subscript at line {}: {}".format(node.lineno, exc))
            params, links = {}, []
            for socket_name, operand in (("value", receiver), ("index", index)):
                if operand[0] == "literal":
                    params[socket_name] = operand[1]
                else:
                    links.append((socket_name, operand[1], operand[2]))
            created = add_node(_PYTHON_SUBSCRIPT_ID, params, links, node.lineno)
            return ("ref", created, "result")
        if isinstance(node, ast.UnaryOp):
            methods = {
                ast.USub: ("__neg__", operator.neg),
                ast.UAdd: ("__pos__", operator.pos),
                ast.Invert: ("__invert__", operator.invert),
            }
            operation = methods.get(type(node.op))
            if operation is None:
                raise ValueError("Unsupported unary operator at line {}".format(node.lineno))
            method, apply_operator = operation
            operand = expression(node.operand)
            # Unary operators on Python literals are ordinary compile-time values,
            # not graph operations. This is required for common syntax such as
            # items[-1], negative dimensions, and signed numeric arguments.
            if operand[0] == "literal":
                try:
                    return ("literal", apply_operator(materialize_literal(operand[1])))
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        "Invalid {} operand at line {}: {}".format(method, node.lineno, exc)
                    )
            definition_id = next((key for key in definitions if ".operator." + method in key), None)
            if not definition_id or operand[0] != "ref":
                raise ValueError("No compatible graph node for unary operator at line {}".format(node.lineno))
            definition = definitions[definition_id]
            receiver_socket = next((item for item in definition.get("inputs", []) if item.get("name") in {"self", "cls"}), None)
            if receiver_socket is None:
                raise ValueError("Unary operator has no receiver socket")
            created = add_node(definition_id, {}, [(receiver_socket["name"], operand[1], operand[2])], node.lineno)
            return ("ref", created, "result")
        if isinstance(node, ast.BinOp):
            # Keep this table aligned with Python's complete binary-operator AST.
            # The importer must not silently assume arithmetic means only + - * /.
            methods = {
                ast.Add: "__add__", ast.Sub: "__sub__", ast.Mult: "__mul__",
                ast.Div: "__truediv__", ast.FloorDiv: "__floordiv__",
                ast.Mod: "__mod__", ast.Pow: "__pow__", ast.MatMult: "__matmul__",
                ast.BitAnd: "__and__", ast.BitOr: "__or__", ast.BitXor: "__xor__",
                ast.LShift: "__lshift__", ast.RShift: "__rshift__",
            }
            method = methods.get(type(node.op))
            if not method:
                raise ValueError("Unsupported binary operator at line {}".format(node.lineno))
            left, right = expression(node.left), expression(node.right)
            if left[0] == "literal" and right[0] == "literal":
                operations = {
                    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.MatMult: operator.matmul,
                    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
                    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
                }
                try:
                    return ("literal", operations[type(node.op)](
                        materialize_literal(left[1]), materialize_literal(right[1])
                    ))
                except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
                    raise ValueError("Invalid binary operation at line {}: {}".format(node.lineno, exc))
            definition_id = next((key for key in definitions if ".operator." + method in key), None)
            if not definition_id:
                raise ValueError("No graph node registered for operator {} (line {})".format(method, node.lineno))
            definition = definitions[definition_id]
            params, links = {}, []
            operands = [left, right]
            for socket, operand in zip(definition.get("inputs", []), operands):
                if operand[0] == "literal": params[socket["name"]] = literal_json_value(operand[1])
                else: links.append((socket["name"], operand[1], operand[2]))
            created = add_node(definition_id, params, links, node.lineno)
            return ("ref", created, "result")
        raise ValueError("Unsupported expression '{}' at line {}".format(
            type(node).__name__, getattr(node, "lineno", "?")))

    def encode_scope_value(value):
        if isinstance(value, enum.Enum):
            return {"__flow_enum__": [value.__class__.__module__,
                                      value.__class__.__name__, value.name]}
        if isinstance(value, b3d.Plane):
            return {
                "__flow_object__": "Plane",
                "origin": list(value.origin), "x_dir": list(value.x_dir),
                "z_dir": list(value.z_dir),
            }
        if isinstance(value, b3d.Location):
            return {"__flow_object__": "Location", "value": list(value.to_tuple())}
        if isinstance(value, b3d.Vector):
            return {"__flow_object__": "Vector", "value": list(value)}
        if isinstance(value, b3d.Axis):
            return {"__flow_object__": "Axis",
                    "args": [list(value.position), list(value.direction)], "kwargs": {}}
        if isinstance(value, (list, tuple)):
            return [encode_scope_value(item) for item in value]
        if isinstance(value, dict):
            return {key: encode_scope_value(item) for key, item in value.items()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("Unsupported context-manager argument value: " + type(value).__name__)

    def scope_expression(node):
        kind, value, *extra = expression(node)
        if kind == "literal":
            return encode_scope_value(value)
        if kind == "ref":
            return {"__flow_ref__": [value, extra[0]]}
        raise ValueError("Unsupported context-manager expression at line {}".format(node.lineno))

    def scope_reference_ids(value):
        references = []

        def visit(item):
            if isinstance(item, dict):
                if "__flow_ref__" in item:
                    references.append(str(item["__flow_ref__"][0]))
                    return
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return list(dict.fromkeys(references))

    def resolve_context_manager(node):
        if not isinstance(node, ast.Call):
            raise ValueError("A with-block must call a build123d context manager (line {})".format(node.lineno))
        function = node.func
        manager = None
        if isinstance(function, ast.Name):
            manager_name = imported.get(function.id, function.id)
            manager = getattr(b3d, manager_name, None)
        elif isinstance(function, ast.Attribute):
            parts = []
            current = function
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name) and current.id in aliases and aliases[current.id] == "b3d":
                path = list(reversed(parts))
                if len(path) == 1:
                    manager = getattr(b3d, path[0], None)
        if not inspect.isclass(manager) or getattr(manager, "__module__", "").split(".")[0] != "build123d":
            raise ValueError("Unsupported build123d context manager at line {}: {}".format(
                node.lineno, ast.unparse(function)))
        supports_context = any(
            "__enter__" in vars(owner) and "__exit__" in vars(owner)
            for owner in getattr(manager, "__mro__", (manager,))
        )
        if not supports_context:
            raise ValueError("{} is not a context manager (line {})".format(
                manager.__name__, node.lineno))
        args = [scope_expression(argument) for argument in node.args]
        kwargs = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise ValueError("Expanded context-manager arguments are unsupported (line {})".format(node.lineno))
            kwargs[keyword.arg] = scope_expression(keyword.value)
        return manager, args, kwargs

    def add_scope_node(scope_id, role, line, output_type="Any", visible=False, dependencies=()):
        nonlocal counter
        counter += 1
        node_id = "import_{}".format(counter)
        definition = {
            "id": "core.python.scope." + role,
            "name": "Python Scope " + role.title(),
            "source": "python", "kind": "value",
            "category": "Python", "palette_category": "Scripting",
            "palette_section": "Execution",
            "description": "Internal value node used to preserve a Python context-manager scope.",
            "inputs": [], "outputs": [{"name": "result", "type": output_type}],
        }
        data = {
            "definition": definition, "params": {},
            "execution_scope_path": list(active_scope_ids),
            "visible": visible,
        }
        if role == "binding":
            data["scope_binding"] = scope_id
            data["scope_dependencies"] = list(dict.fromkeys(dependencies))
        else:
            data["scope_result"] = scope_id
        graph_nodes.append({
            "id": node_id, "type": "api",
            "position": {"x": 80 + ((counter - 1) % 4) * 300, "y": 80 + ((counter - 1) // 4) * 190},
            "data": data,
        })
        return node_id

    def process_statements(statements):
        nonlocal scope_counter
        for statement in statements:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(statement, ast.With):
                entered_contexts = []
                for item in statement.items:
                    manager, args, kwargs = resolve_context_manager(item.context_expr)
                    scope_counter += 1
                    scope_id = "python_scope_{}".format(scope_counter)
                    manager_name = manager.__name__
                    result_attribute = {
                        "BuildPart": "part", "BuildSketch": "sketch", "BuildLine": "line",
                    }.get(manager_name)
                    optional = item.optional_vars
                    if optional is not None and not isinstance(optional, ast.Name):
                        raise ValueError("Only a simple name is supported after 'as' (line {})".format(statement.lineno))
                    descriptor = {
                        "id": scope_id, "module": manager.__module__,
                        "name": manager_name, "args": args, "kwargs": kwargs,
                        "result_attribute": result_attribute,
                        "has_binding": optional is not None,
                    }
                    execution_scopes.append(descriptor)
                    dependencies = scope_reference_ids([args, kwargs])
                    active_scope_ids.append(scope_id)
                    binding_node = add_scope_node(
                        scope_id, "binding", statement.lineno,
                        dependencies=dependencies,
                    )
                    if optional is not None:
                        variables[optional.id] = ("ref", binding_node, "result")
                    entered_contexts.append((scope_id, manager_name, optional))
                process_statements(statement.body)
                for scope_id, manager_name, optional in reversed(entered_contexts):
                    active_scope_ids.pop()
                    output_type = {
                        "BuildPart": "Shape", "BuildSketch": "Sketch", "BuildLine": "Wire",
                    }.get(manager_name, "Any")
                    should_emit_result = optional is not None or manager_name in {"BuildPart", "BuildSketch", "BuildLine"}
                    if should_emit_result:
                        result_node = add_scope_node(
                            scope_id, "result", statement.lineno, output_type,
                            output_type in {"Shape", "Sketch", "Wire"}
                        )
                        if optional is not None:
                            variables[optional.id] = ("ref", result_node, "result")
                continue
            if isinstance(statement, ast.Expr):
                if is_viewer_call(statement.value):
                    continue
                expression(statement.value)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value_node = statement.value
                if value_node is None:
                    continue
                result = expression(value_node)
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        raise ValueError("Only simple variable assignments are supported (line {})".format(statement.lineno))
                    variables[target.id] = result
                continue
            if isinstance(statement, ast.Pass):
                continue
            raise ValueError("Unsupported statement '{}' at line {}".format(
                type(statement).__name__, getattr(statement, "lineno", "?")))

    process_statements(tree.body)
    if not graph_nodes:
        raise ValueError("No supported build123d operations were found")
    return {"nodes": graph_nodes, "edges": graph_edges, "execution_scopes": execution_scopes}

def export_python_code(payload):
    """Compile a graph to standalone build123d Python using native API calls."""
    graph = json.loads(payload) if isinstance(payload, str) else payload
    nodes = {str(node["id"]): node for node in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    if not nodes:
        raise ValueError("The canvas is empty")

    # _graph_plan raises ValueError for duplicate inputs and cycles.
    incoming, outgoing, order = _graph_plan(nodes, edges)

    imports = []
    statements = []
    variables = {node_id: "_node_{}".format(index + 1) for index, node_id in enumerate(order)}
    execution_scopes = {item.get("id"): item for item in graph.get("execution_scopes", [])}
    active_export_scopes = []
    unsupported = []

    def transition_export_scopes(target_path):
        common = 0
        while common < len(active_export_scopes) and common < len(target_path):
            if active_export_scopes[common] != target_path[common]:
                break
            common += 1
        del active_export_scopes[common:]
        for scope_id in target_path[common:]:
            descriptor = execution_scopes.get(scope_id)
            if descriptor is None:
                raise ValueError("Unknown Python execution scope: " + str(scope_id))
            manager_name = descriptor["name"]
            def scope_code_value(value):
                if isinstance(value, list):
                    return "[" + ", ".join(scope_code_value(item) for item in value) + "]"
                if isinstance(value, dict):
                    if "__flow_ref__" in value:
                        source_id, output_name = value["__flow_ref__"]
                        return variables[source_id] + ("" if output_name == "result" else "." + output_name)
                    if "__flow_enum__" in value:
                        _, class_name, member_name = value["__flow_enum__"]
                        return "b3d.{}.{}".format(class_name, member_name)
                    if "__flow_object__" in value:
                        object_type = value["__flow_object__"]
                        if "args" in value or "kwargs" in value:
                            args = [scope_code_value(item) for item in value.get("args", [])]
                            kwargs = ["{}={}".format(key, scope_code_value(item))
                                      for key, item in value.get("kwargs", {}).items()]
                            return "b3d.{}({})".format(object_type, ", ".join(args + kwargs))
                        if object_type == "Plane":
                            fields = ["{}={}".format(key, scope_code_value(value[key]))
                                      for key in ("origin", "x_dir", "z_dir")]
                            return "b3d.Plane(" + ", ".join(fields) + ")"
                        if object_type == "Location":
                            return "b3d.Location(" + scope_code_value(value["value"]) + ")"
                        return "b3d.Vector(" + ", ".join(scope_code_value(item) for item in value["value"]) + ")"
                    return "{" + ", ".join(repr(key) + ": " + scope_code_value(item)
                                            for key, item in value.items()) + "}"
                return repr(value)
            args = [scope_code_value(value) for value in descriptor.get("args", [])]
            kwargs = ["{}={}".format(key, scope_code_value(value))
                      for key, value in descriptor.get("kwargs", {}).items()]
            manager_var = "_scope_" + str(len(active_export_scopes) + 1)
            call = "b3d.{}({})".format(manager_name, ", ".join(args + kwargs))
            statements.append("    " * len(active_export_scopes) + "with " + call + " as " + manager_var + ":")
            active_export_scopes.append(scope_id)
            scope_manager_variables[scope_id] = manager_var

    scope_manager_variables = {}
    for node_id in order:
        node = nodes[node_id]
        data = node.get("data", {})
        transition_export_scopes(data.get("execution_scope_path", []))
        if data.get("scope_binding"):
            variables[node_id] = scope_manager_variables[data["scope_binding"]]
            continue
        if data.get("scope_result"):
            scope_id = data["scope_result"]
            descriptor = execution_scopes[scope_id]
            manager_var = scope_manager_variables[scope_id]
            result_attr = descriptor.get("result_attribute")
            expression = manager_var + ("." + result_attr if result_attr else "")
            statements.append("    " * len(active_export_scopes) + variables[node_id] + " = " + expression)
            statements.append("")
            continue
        definition = _CATALOG.get(data.get("definition", {}).get("id"))
        if definition is None:
            raise ValueError("Node {} refers to an unknown API definition".format(node_id))
        source = definition.get("source")
        if source not in {"build123d", "flow"}:
            unsupported.append(definition["name"] + " (" + source + ")")
            continue
        is_preview_style = definition.get("id") == "core.preview.style"
        if source == "flow" and (definition.get("kind") == "value" or is_preview_style):
            pass
        elif source != "build123d":
            unsupported.append(definition["name"] + " (Flow utility)")
            continue

        params = dict(data.get("params", {}))
        for socket in definition.get("inputs", []):
            edge = incoming.get(node_id, {}).get(socket["name"])
            if not edge and socket["name"] not in params and "default" in socket:
                params[socket["name"]] = socket["default"]

        def value_expression(socket):
            edge = incoming.get(node_id, {}).get(socket["name"])
            if edge:
                upstream, output_name = edge
                return variables[upstream] + (
                    "" if output_name == "result" else "." + output_name
                )
            value = params.get(socket["name"], _MISSING)
            if value is _MISSING:
                if socket.get("required", False):
                    raise ValueError(
                        "Node {} is missing required input '{}'".format(
                            definition["name"], socket["name"]
                        )
                    )
                return None
            annotation = _python_node_annotation(definition, socket)
            return _python_code_literal(value, annotation)

        # Preview styling is omitted from exported model code; it is a pass-through.
        if source == "flow" and definition.get("kind") == "value":
            socket = definition.get("inputs", [{}])[0]
            expression = value_expression(socket)
        elif is_preview_style:
            expression = value_expression(next(socket for socket in definition["inputs"] if socket["name"] == "shape"))
        else:
            api_id = definition["id"].split(".overload-", 1)[0]
            if ".operator." in api_id:
                owner_path, method_name = api_id.split(".operator.", 1)
                sockets = definition.get("inputs", [])
                receiver_socket = next((item for item in sockets if item["name"] in {"self", "cls"}), None)
                if receiver_socket is None:
                    raise ValueError("Operator node {} has no receiver input".format(definition["name"]))
                receiver = value_expression(receiver_socket)
                args = [item for item in sockets if item is not receiver_socket]
                operands = [value_expression(item) for item in args]
                symbol = _PYTHON_OPERATOR_SPECS.get(method_name, {}).get("codegen_symbol")
                if symbol is None:
                    raise ValueError("Unsupported native operator: " + method_name)
                if method_name in {"__neg__", "__pos__", "__invert__"}:
                    expression = "({}{})".format(symbol, receiver)
                elif method_name == "__rmul__":
                    expression = "({} * {})".format(operands[0], receiver)
                else:
                    if not operands or operands[0] is None:
                        raise ValueError("Operator {} is missing its operand".format(method_name))
                    expression = "({} {} {})".format(receiver, symbol, operands[0])
            elif definition["id"] == "core.python.attribute":
                receiver = value_expression(next(item for item in definition["inputs"] if item["name"] == "value"))
                member_name = params.get("name")
                expression = "getattr({}, {})".format(receiver, repr(member_name))
            elif ".property." in api_id or ".field." in api_id:
                marker = ".property." if ".property." in api_id else ".field."
                _, member_name = api_id.split(marker, 1)
                receiver_socket = definition["inputs"][0]
                receiver = value_expression(receiver_socket)
                expression = "{}.{}".format(receiver, member_name)
            elif (
                _CATALOG_SIGNATURES.get(definition["id"]) is not None
                and next(iter(_CATALOG_SIGNATURES[definition["id"]].parameters.values()), None) is not None
                and next(iter(_CATALOG_SIGNATURES[definition["id"]].parameters.values())).name in {"self", "cls"}
            ):
                member_name = api_id.rsplit(".", 1)[-1]
                receiver_socket = next((item for item in definition["inputs"] if item["name"] in {"self", "cls"}), None)
                if receiver_socket is None:
                    raise ValueError("Method node {} has no receiver input".format(definition["name"]))
                receiver = value_expression(receiver_socket)
                arguments = []
                for socket in definition["inputs"]:
                    if socket is receiver_socket:
                        continue
                    expression_value = value_expression(socket)
                    if expression_value is None:
                        continue
                    kind = socket.get("kind")
                    if kind == "var_positional":
                        arguments.append("*" + expression_value)
                    elif kind == "var_keyword":
                        arguments.append("**" + expression_value)
                    else:
                        arguments.append("{}={}".format(socket["name"], expression_value))
                expression = "{}.{}({})".format(receiver, member_name, ", ".join(arguments))
            else:
                callable_obj = _CATALOG_CALLABLES.get(definition["id"])
                if callable_obj is None:
                    raise ValueError("No native callable registered for " + definition["id"])
                callable_ref = _python_import_reference(
                    callable_obj, imports, "_api_callable_{}".format(len(imports))
                )
                arguments = []
                for socket in definition.get("inputs", []):
                    expression_value = value_expression(socket)
                    if expression_value is None:
                        continue
                    kind = socket.get("kind")
                    if kind == "var_positional":
                        arguments.append("*" + expression_value)
                    elif kind == "var_keyword":
                        arguments.append("**" + expression_value)
                    else:
                        arguments.append("{}={}".format(socket["name"], expression_value))
                expression = "{}({})".format(callable_ref, ", ".join(arguments))

        statements.append("    " * len(active_export_scopes) + "{} = {}".format(variables[node_id], expression))
        statements.append("")

    active_export_scopes.clear()

    if unsupported:
        raise ValueError(
            "Python export currently requires build123d API nodes and literal value nodes. "
            "Unsupported nodes: " + ", ".join(sorted(set(unsupported)))
        )

    terminal_nodes = [node_id for node_id in order if not outgoing[node_id]]
    if terminal_nodes:
        statements.append("result = " + variables[terminal_nodes[-1]])
    header = [
        '"""Generated by build123d-flow."""',
        "import build123d as b3d",
    ]
    return "\n".join(header + imports + ([""] if imports else []) + statements).rstrip() + "\n"


_MISSING = object()


def export_step_bytes():
    if _LAST_SHAPE is None:
        return None
    import io
    stream = io.BytesIO()
    b3d.export_step(_LAST_SHAPE, stream)
    return stream.getvalue()


discover(include_full=False)