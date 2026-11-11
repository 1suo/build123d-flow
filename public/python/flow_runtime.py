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

import build123d as b3d

_CATALOG = {}
_CATALOG_CALLABLES = {}
_CATALOG_SIGNATURES = {}
_CATALOG_HINT_TARGETS = {}
_LAST_VALUES = {}
_LAST_SHAPE = None
_DISCOVERY_REPORT = {}


def _type_name(annotation):
    if annotation is inspect.Signature.empty or annotation is typing.Any:
        return "Any"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        names = {_type_name(arg) for arg in args if arg is not type(None)}
        if names in ({"Rotation", "List"}, {"Rotation", "Tuple"}):
            return "Rotation"
        return names.pop() if len(names) == 1 else "Any"
    if origin is tuple:
        return "Tuple"
    if origin in (set, frozenset):
        return "Set"
    if origin in (list, collections.abc.Sequence, collections.abc.Iterable):
        return "List"
    if origin in (dict, typing.Dict, collections.abc.Mapping):
        return "Dict"
    if origin is typing.Literal:
        return "Enum"
    if origin is typing.Annotated:
        return _type_name(args[0]) if args else "Any"
    if inspect.isclass(annotation):
        try:
            if issubclass(annotation, enum.Enum):
                return "Enum"
            for shape_name in ("Solid", "Compound", "Face", "Wire", "Edge", "Shell", "Sketch", "Part"):
                shape_class = getattr(b3d, shape_name, None)
                if inspect.isclass(shape_class) and issubclass(annotation, shape_class):
                    return shape_name
            if issubclass(annotation, getattr(b3d, "Shape", object)):
                return "Shape"
        except TypeError:
            pass
    if isinstance(annotation, str):
        name = annotation.strip().strip("'\\\"").split(".")[-1].lower()
    else:
        name = getattr(annotation, "__name__", str(annotation)).lower()
    mapping = {
        "int": "Number", "float": "Number", "bool": "Boolean", "str": "String",
        "vector": "Vector3", "vector2": "Vector2", "vector3": "Vector3",
        "rotation": "Rotation", "plane": "Plane", "location": "Location", "axis": "Axis", "color": "Color",
        "shape": "Shape", "solid": "Solid", "compound": "Compound", "face": "Face",
        "edge": "Edge", "wire": "Wire", "shell": "Shell", "part": "Part",
        "sketch": "Sketch", "code": "Code", "callable": "Code",
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
    defaults = {"step": 1, "count": 10, "stop": 10, "index": 0, "start": 0, "default": None}
    found[node_id] = {
        "id": node_id,
        "kind": "value" if not inputs else "operation",
        "name": name,
        "category": category,
        "description": description,
        "inputs": [
            {
                "name": key, "type": socket_type, "annotation": socket_type,
                "required": socket_type not in {"Number", "Boolean", "String", "List", "Tuple", "Set", "Dict"} and key != "default",
                **({"item_type": "Any"} if socket_type in {"List", "Tuple", "Set"} else {}),
                **({"key_type": "Any", "value_type": "Any"} if socket_type == "Dict" else {}),
                **({"default": defaults.get(key, {"Number": 0.0, "Boolean": False, "String": "", "List": [], "Tuple": [], "Set": [], "Dict": {}}[socket_type])}
                   if socket_type in {"Number", "Boolean", "String", "List", "Tuple", "Set", "Dict"} else
                   ({"default": None} if key == "default" else {})),
            }
            for key, socket_type in inputs
        ],
        "outputs": [{
            "name": "result", "type": output_type, "annotation": output_type,
            **({"item_type": "Any"} if output_type in {"List", "Tuple", "Set"} else {}),
            **({"key_type": "Any", "value_type": "Any"} if output_type == "Dict" else {}),
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

    # First-class collection construction and transformation nodes.
    _add_node(found, callables, "core.list.construct", "List", "Collections",
              lambda items: list(items), [("items", "List")], "List",
              "Create a list from an input sequence.")
    _add_node(found, callables, "core.list.length", "List Length", "Collections",
              len, [("items", "List")], "Number")
    _add_node(found, callables, "core.list.get", "Get Item", "Collections",
              lambda items, index: items[index], [("items", "List"), ("index", "Number")], "Any")
    _add_node(found, callables, "core.list.set", "Set Item", "Collections",
              lambda items, index, value: (lambda result: (result.__setitem__(index, value), result)[1])(list(items)),
              [("items", "List"), ("index", "Number"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.append", "Append", "Collections",
              lambda items, value: list(items) + [value], [("items", "List"), ("value", "Any")], "List")
    _add_node(found, callables, "core.list.extend", "Extend", "Collections",
              lambda items, values: list(items) + list(values), [("items", "List"), ("values", "List")], "List")
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
    _add_node(found, callables, "core.list.flatten", "Flatten", "Collections",
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
              [("start", "Number"), ("stop", "Number"), ("step", "Number")], "List")
    _add_node(found, callables, "core.series.linspace", "Linspace", "Series",
              lambda start, stop, count: ([float(start)] if int(count) <= 1 else
                  [float(start) + (float(stop) - float(start)) * i / (int(count) - 1) for i in range(int(count))]),
              [("start", "Number"), ("stop", "Number"), ("count", "Number")], "List")
    _add_node(found, callables, "core.series.arithmetic", "Arithmetic Series", "Series",
              lambda start, step, count: [float(start) + i * float(step) for i in range(int(count))],
              [("start", "Number"), ("step", "Number"), ("count", "Number")], "List")
    _add_node(found, callables, "core.series.geometric", "Geometric Series", "Series",
              lambda start, ratio, count: [float(start) * float(ratio) ** i for i in range(int(count))],
              [("start", "Number"), ("ratio", "Number"), ("count", "Number")], "List")

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
              [("value", "String"), ("separator", "String")], "List")
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
    _add_node(found, callables, "core.tuple.construct", "Tuple", "Collections",
              tuple, [("items", "List")], "Tuple")
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
    _add_node(found, callables, "core.set.construct", "Set", "Collections",
              set, [("items", "List")], "Set")
    _add_node(found, callables, "core.set.union", "Set Union", "Collections",
              lambda a, b: set(a) | set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.intersection", "Set Intersection", "Collections",
              lambda a, b: set(a) & set(b), [("a", "Set"), ("b", "Set")], "Set")
    _add_node(found, callables, "core.set.difference", "Set Difference", "Collections",
              lambda a, b: set(a) - set(b), [("a", "Set"), ("b", "Set")], "Set")
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
    global _CATALOG, _CATALOG_CALLABLES, _CATALOG_SIGNATURES, _CATALOG_HINT_TARGETS
    found = {}
    callables = {}
    signatures = {}
    hint_targets = {}
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
                    hints = typing.get_type_hints(hint_target)
                except Exception:
                    hints = {}

                for parameter in signature.parameters.values():
                    annotation = hints.get(parameter.name, parameter.annotation)
                    if parameter.kind == parameter.VAR_POSITIONAL:
                        default, default_repr, required, has_default = [], "()", False, True
                        socket_type = "List"
                    elif parameter.kind == parameter.VAR_KEYWORD:
                        default, default_repr, required, has_default = {}, "{}", False, True
                        socket_type = "Any"
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
                    if typing.get_origin(annotation) in (list, tuple, set, frozenset):
                        item_args = typing.get_args(annotation)
                        if item_args:
                            socket["item_type"] = _type_name(item_args[0])
                    if typing.get_origin(annotation) in (dict, typing.Dict, collections.abc.Mapping):
                        mapping_args = typing.get_args(annotation)
                        socket["key_type"] = _type_name(mapping_args[0]) if mapping_args else "String"
                        socket["value_type"] = _type_name(mapping_args[1]) if len(mapping_args) > 1 else "Any"
                    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
                        socket["variants"] = sorted({_type_name(arg) for arg in typing.get_args(annotation) if arg is not type(None)})
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
                    typed_args = ", ".join(
                        parameter.name + ": " + _type_name(hints.get(parameter.name, parameter.annotation))
                        for parameter in signature.parameters.values()
                    )
                    display_name = name + "(" + ", ".join(parameter.name for parameter in signature.parameters.values()) + ")"

                module_parts = canonical_module.split(".")
                category = module_parts[-1].replace("_", " ").title() if len(module_parts) > 1 else "Build123d"
                doc = inspect.getdoc(obj) or ""
                found[overload_id] = {
                    "id": overload_id,
                    "kind": node_kind,
                    "name": display_name,
                    "category": category,
                    "description": doc.split("\\n")[0][:180],
                    "inputs": inputs,
                    "outputs": [{
                        "name": "result", "type": output_type, "annotation": "",
                        **({"variants": sorted({_type_name(arg) for arg in typing.get_args(hints.get("return", signature.return_annotation)) if arg is not type(None)})}
                           if typing.get_origin(hints.get("return", signature.return_annotation)) in (typing.Union, types.UnionType) else {}),
                    }],
                }
                callables[overload_id] = obj
                signatures[overload_id] = signature
                hint_targets[overload_id] = hint_target
                report["accepted"].append({"id": overload_id, "kind": node_kind})

    # Discover useful instance methods on actual B-rep classes as operation nodes.
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
            try:
                hints = typing.get_type_hints(method)
            except Exception:
                hints = {}
            inputs = [{
                "name": "self", "label": "shape", "type": _type_name(cls),
                "annotation": str(cls), "required": True,
            }]
            for parameter in parameters[1:]:
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
                if typing.get_origin(annotation) in (list, tuple, set, frozenset):
                    item_args = typing.get_args(annotation)
                    if item_args:
                        socket["item_type"] = _type_name(item_args[0])
                if typing.get_origin(annotation) in (typing.Union, types.UnionType):
                    socket["variants"] = sorted({_type_name(arg) for arg in typing.get_args(annotation) if arg is not type(None)})
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
                "id": node_id, "kind": "operation", "name": method_name,
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


def _inspect_value(value, depth=0):
    value_type = type(value).__name__
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"type": value_type, "value": value}
    if depth >= 5:
        return {"type": value_type, "repr": repr(value)[:1000], "truncated": True}
    if isinstance(value, dict):
        items = {}
        for key, item in list(value.items())[:200]:
            items[str(key)] = _inspect_value(item, depth + 1)
        return {"type": value_type, "items": items, "length": len(value)}
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        return {"type": value_type, "items": [_inspect_value(item, depth + 1) for item in sequence[:200]], "length": len(sequence)}
    attributes = {}
    try:
        names = [name for name in dir(value) if not name.startswith("_")]
        for name in names[:80]:
            try:
                item = getattr(value, name)
                if callable(item):
                    continue
                if isinstance(item, (bool, int, float, str, list, tuple, dict)) or item is None:
                    attributes[name] = _inspect_value(item, depth + 1)
            except Exception:
                continue
    except Exception:
        pass
    return {"type": value_type, "repr": repr(value)[:2000], "attributes": attributes}


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
            result = _call_with_coercion(
                callable_obj, params,
                signature=_CATALOG_SIGNATURES.get(definition_id),
                hint_target=_CATALOG_HINT_TARGETS.get(definition_id),
            )
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
    return json.dumps(_json_clean({"previews": previews, "errors": errors, "order": order, "socket_values": {node_id: {name: _inspect_value(value) for name, value in outputs.items()} for node_id, outputs in values.items()}}), allow_nan=False)



def _call_with_coercion(callable_obj, params, signature=None, hint_target=None):
    target = hint_target or (callable_obj.__init__ if inspect.isclass(callable_obj) else callable_obj)
    try:
        hints = typing.get_type_hints(target)
    except Exception:
        hints = {}
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
        annotation_candidates = typing.get_args(annotation) if typing.get_origin(annotation) in (typing.Union, types.UnionType) else (annotation,)
        resolved_candidates = []
        for candidate in annotation_candidates:
            if isinstance(candidate, str):
                try:
                    candidate = eval(candidate, getattr(target, "__globals__", {}))
                except Exception:
                    pass
            resolved_candidates.append(candidate)

        # Browser structured editors serialize rotations as arrays. Convert
        # those arrays to Build123d Rotation objects before they reach OCCT
        # placement code, which expects a wrapped rotation rather than a list.
        is_rotation_like = (
            isinstance(value, (list, tuple))
            and any(inspect.isclass(candidate) and candidate.__name__ == "Rotation" for candidate in resolved_candidates)
            and any(typing.get_origin(candidate) is tuple for candidate in resolved_candidates)
        )
        if is_rotation_like:
            converted[name] = b3d.Rotation(tuple(value))
            continue

        annotation = next((candidate for candidate in resolved_candidates if inspect.isclass(candidate)), annotation)
        if isinstance(value, str) and inspect.isclass(annotation):
            try:
                if issubclass(annotation, enum.Enum):
                    converted[name] = annotation[value]
                    continue
            except (TypeError, KeyError):
                pass
        origin = typing.get_origin(annotation)
        if origin is tuple and isinstance(value, list):
            converted[name] = tuple(value)
            value = converted[name]
        elif origin is set and isinstance(value, list):
            converted[name] = set(value)
            value = converted[name]
        elif origin is list and not isinstance(value, list):
            converted[name] = list(value)
            value = converted[name]
        elif origin in (dict, collections.abc.Mapping) and isinstance(value, dict):
            converted[name] = dict(value)
            value = converted[name]
        if inspect.isclass(annotation):
            type_name = annotation.__name__.lower()
            if type_name == "vector" and isinstance(value, (list, tuple)):
                converted[name] = b3d.Vector(*value)
            elif type_name == "rotation" and isinstance(value, (list, tuple)):
                converted[name] = b3d.Rotation(tuple(value))
            elif type_name == "plane" and isinstance(value, dict):
                converted[name] = b3d.Plane(**value)
            elif type_name == "location" and isinstance(value, dict):
                converted[name] = b3d.Location(**value)
            elif type_name == "location" and isinstance(value, (list, tuple)):
                converted[name] = b3d.Location(*value)

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
