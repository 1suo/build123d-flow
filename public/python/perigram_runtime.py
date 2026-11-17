"""Perigram browser-runtime value transport and persistent Python scope sessions.

Native Python/CAD objects never cross the worker boundary. They are retained in a
bounded runtime-owned store and represented to the host by opaque session handles.
"""

from __future__ import annotations

import importlib
import json
import math
import operator
import uuid
from collections import OrderedDict
from typing import Any, Mapping

_MARKER = "__perigram_runtime__"
_MAX_HANDLES = 8192
_MAX_SESSIONS = 16

_VALUE_STORE: dict[str, Any] = {}
_OBJECT_REFS: dict[tuple[int, str], str] = {}
_REF_COUNTS: dict[str, int] = {}
_TOKEN_SESSIONS: dict[str, str] = {}
_SESSIONS: "OrderedDict[str, _Session]" = OrderedDict()


def _type_descriptor(value: Any) -> dict[str, str]:
    cls = type(value)
    module_name = getattr(cls, "__module__", "builtins") or "builtins"
    type_name = getattr(cls, "__name__", "object") or "object"
    return {
        "typeId": module_name + "." + type_name,
        "provider": "build123d" if module_name.split(".")[0] == "build123d" else "perigram.python",
    }


def _store_value(value: Any, session_id: str) -> dict[str, Any]:
    object_key = (id(value), session_id)
    token = _OBJECT_REFS.get(object_key)
    if token is None or _VALUE_STORE.get(token) is not value:
        if len(_VALUE_STORE) >= _MAX_HANDLES:
            _evict_unreferenced()
        if len(_VALUE_STORE) >= _MAX_HANDLES:
            raise RuntimeError(
            "Perigram Python value store is full; release or restart the plugin runtime"
            )
        token = uuid.uuid4().hex
        _OBJECT_REFS[object_key] = token
        _VALUE_STORE[token] = value
        _REF_COUNTS[token] = 0
        _TOKEN_SESSIONS[token] = session_id
    descriptor = _type_descriptor(value)
    return {
        _MARKER: "value-ref",
        "id": token,
        "session": session_id,
        **descriptor,
    }


def _encode(value: Any, session_id: str) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 9007199254740991:
            return {_MARKER: "bigint", "value": str(value)}
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite Python floats cannot cross the Perigram boundary")
        return value
    # Only exact built-in containers are structural. Nominal subclasses such as
    # build123d.ShapeList remain opaque objects so their Python behavior survives.
    if type(value) is list:
        return [_encode(item, session_id) for item in value]
    if type(value) is tuple:
        return {_MARKER: "tuple", "items": [_encode(item, session_id) for item in value]}
    if type(value) is set:
        return {_MARKER: "set", "items": [_encode(item, session_id) for item in value]}
    if type(value) is frozenset:
        return {_MARKER: "frozenset", "items": [_encode(item, session_id) for item in value]}
    if type(value) is dict:
        if all(isinstance(key, str) for key in value) and _MARKER not in value:
            return {key: _encode(item, session_id) for key, item in value.items()}
        return {
            _MARKER: "map",
            "entries": [
                [_encode(key, session_id), _encode(item, session_id)]
                for key, item in value.items()
            ],
        }
    return _store_value(value, session_id)


def _decode(value: Any, session_id: str) -> Any:
    if isinstance(value, list):
        return [_decode(item, session_id) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get(_MARKER)
    if marker == "value-ref":
        if value.get("session") != session_id:
            raise ValueError("Opaque Python value belongs to a different graph session")
        token = value.get("id")
        if token not in _VALUE_STORE:
            raise ValueError("Opaque Python value reference is expired or unknown")
        return _VALUE_STORE[token]
    if marker == "bigint":
        return int(value["value"])
    if marker == "tuple":
        return tuple(_decode(item, session_id) for item in value.get("items", []))
    if marker == "set":
        return set(_decode(item, session_id) for item in value.get("items", []))
    if marker == "frozenset":
        return frozenset(_decode(item, session_id) for item in value.get("items", []))
    if marker == "map":
        return {
            _decode(pair[0], session_id): _decode(pair[1], session_id)
            for pair in value.get("entries", [])
        }
    if marker is not None:
        raise ValueError("Unknown Perigram Python transport marker: " + str(marker))
    return {key: _decode(item, session_id) for key, item in value.items()}


def _collect_refs(value: Any, found: set[str] | None = None) -> set[str]:
    found = found if found is not None else set()
    if isinstance(value, list):
        for item in value:
            _collect_refs(item, found)
    elif isinstance(value, dict):
        if value.get(_MARKER) == "value-ref" and value.get("id"):
            found.add(str(value["id"]))
        else:
            for item in value.values():
                _collect_refs(item, found)
    return found


def _evict_unreferenced() -> None:
    for token in list(_VALUE_STORE):
        if _REF_COUNTS.get(token, 0) > 0:
            continue
        value = _VALUE_STORE.pop(token, None)
        _REF_COUNTS.pop(token, None)
        session_id = _TOKEN_SESSIONS.pop(token, "")
        if value is not None:
            _OBJECT_REFS.pop((id(value), session_id), None)


def _set_node_output(session: "_Session", node_id: str, encoded: Any) -> None:
    previous = session.node_outputs.get(node_id)
    if previous is not None:
        for token in _collect_refs(previous):
            _REF_COUNTS[token] = max(0, _REF_COUNTS.get(token, 0) - 1)
    session.node_outputs[node_id] = encoded
    for token in _collect_refs(encoded):
        _REF_COUNTS[token] = _REF_COUNTS.get(token, 0) + 1
    _evict_unreferenced()


def _restore_scope_value(value: Any, session: "_Session", flow_runtime: Any) -> Any:
    if isinstance(value, list):
        return [_restore_scope_value(item, session, flow_runtime) for item in value]
    if not isinstance(value, dict):
        return value
    if "__flow_tuple__" in value:
        return tuple(_restore_scope_value(item, session, flow_runtime) for item in value["__flow_tuple__"])
    if "__flow_attrgetter__" in value:
        return operator.attrgetter(value["__flow_attrgetter__"])
    if "__flow_callable__" in value:
        module_name, qualified_name = value["__flow_callable__"]
        target = importlib.import_module(module_name)
        for part in qualified_name.split("."):
            target = getattr(target, part)
        return target
    if "__flow_ref__" in value:
        source_id, output_name = value["__flow_ref__"]
        source = session.node_outputs.get(str(source_id))
        if source is None:
            raise ValueError("Context argument depends on unavailable node " + str(source_id))
        if output_name not in source:
            raise ValueError(
                "Context argument references missing output "
                + str(source_id) + "." + str(output_name)
            )
        return _decode(source[output_name], session.session_id)
    if "__flow_enum__" in value:
        module_name, class_name, member_name = value["__flow_enum__"]
        enum_type = getattr(importlib.import_module(module_name), class_name)
        return getattr(enum_type, member_name)
    if "__flow_object__" in value:
        object_type = value["__flow_object__"]
        b3d = getattr(flow_runtime, "b3d", None)
        if b3d is None or not hasattr(b3d, object_type):
            raise ValueError("Unavailable build123d literal type: " + str(object_type))
        constructor = getattr(b3d, object_type)
        if "args" in value or "kwargs" in value:
            args = _restore_scope_value(value.get("args", []), session, flow_runtime)
            kwargs = _restore_scope_value(value.get("kwargs", {}), session, flow_runtime)
            return constructor(*args, **kwargs)
        if object_type == "Plane":
            return constructor(
                origin=_restore_scope_value(value["origin"], session, flow_runtime),
                x_dir=_restore_scope_value(value["x_dir"], session, flow_runtime),
                z_dir=_restore_scope_value(value["z_dir"], session, flow_runtime),
            )
        if object_type == "Location":
            return constructor(_restore_scope_value(value["value"], session, flow_runtime))
        return constructor(*_restore_scope_value(value["value"], session, flow_runtime))
    if value.get(_MARKER):
        return _decode(value, session.session_id)
    return {
        key: _restore_scope_value(item, session, flow_runtime)
        for key, item in value.items()
    }


def _restore_scope_arguments(values: Any, session: "_Session", flow_runtime: Any) -> list[Any]:
    arguments = []
    for value in values or []:
        if isinstance(value, dict) and "__flow_starred__" in value:
            arguments.extend(
                _restore_scope_value(value["__flow_starred__"], session, flow_runtime)
            )
        else:
            arguments.append(_restore_scope_value(value, session, flow_runtime))
    return arguments


def _scope_runner(session: "_Session"):
    """Persistent generator frame for build123d's lexical builder scopes."""
    active_scopes = []
    scope_values = {}
    scope_results = {}
    failed_scopes = set()
    command = yield None

    while True:
        scope_path = []
        try:
            if command.get("action") == "dispose":
                while active_scopes:
                    entry = active_scopes.pop()
                    entry["manager"].__exit__(None, None, None)
                command = yield (True, None)
                continue

            import flow_runtime

            config = command.get("config") or {}
            scope_path = list(config.get("executionScopePath") or [])
            scope_definitions = {
                item.get("id"): item
                for item in config.get("executionScopes", [])
                if isinstance(item, dict) and item.get("id")
            }
            if any(scope_id in failed_scopes for scope_id in scope_path):
                raise RuntimeError("Execution skipped because an enclosing Python scope failed")

            common = 0
            while common < len(active_scopes) and common < len(scope_path):
                if active_scopes[common]["id"] != scope_path[common]:
                    break
                common += 1

            def close_scope(scope_id: str, exception_info=(None, None, None)) -> None:
                entry = active_scopes.pop()
                manager = entry["manager"]
                descriptor = scope_definitions.get(scope_id, {})
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

            while len(active_scopes) > common:
                close_scope(active_scopes[-1]["id"])

            for scope_id in scope_path[common:]:
                descriptor = scope_definitions.get(scope_id)
                if descriptor is None:
                    raise ValueError("Unknown Python execution scope: " + str(scope_id))
                failed_scopes.discard(scope_id)
                scope_results.pop(scope_id, None)
                module = importlib.import_module(descriptor["module"])
                manager_type = getattr(module, descriptor["name"])
                manager_args = _restore_scope_arguments(
                    descriptor.get("args", []), session, flow_runtime
                )
                manager_kwargs = _restore_scope_value(
                    descriptor.get("kwargs", {}), session, flow_runtime
                )
                manager = manager_type(*manager_args, **manager_kwargs)
                try:
                    # This call intentionally stays in the persistent generator
                    # frame. build123d detects lexical builder nesting by frame.
                    entered = manager.__enter__()
                except Exception as exc:
                    exception_info = (type(exc), exc, exc.__traceback__)
                    while active_scopes:
                        try:
                            close_scope(active_scopes[-1]["id"], exception_info)
                        except Exception:
                            pass
                    raise
                scope_values[scope_id] = entered
                active_scopes.append({
                    "id": scope_id,
                    "manager": manager,
                    "entered": entered,
                })

            operation_id = command.get("operationId")
            scope_binding = config.get("scopeBinding")
            scope_result = config.get("scopeResult")
            if scope_binding:
                if scope_binding not in scope_values:
                    raise ValueError("Python scope binding was not entered: " + str(scope_binding))
                result = {"result": scope_values[scope_binding]}
            elif scope_result:
                if scope_result in failed_scopes:
                    raise RuntimeError("Scope result unavailable because its body failed")
                result = {"result": scope_results.get(scope_result)}
            else:
                clean_config = dict(config)
                for key in (
                    "executionScopePath", "executionScopes", "scopeBinding",
                    "scopeResult", "scopeDependencies", "scopeDependencyInputs",
                    "nodeId", "graphId", "executionId", "runId",
                ):
                    clean_config.pop(key, None)
                result = flow_runtime.execute_discovered_operation(
                    operation_id,
                    command.get("inputs") or {},
                    clean_config,
                )
            command = yield (True, result)
        except Exception as exc:
            import sys
            exception_info = (type(exc), exc, exc.__traceback__)
            while active_scopes:
                try:
                    entry = active_scopes[-1]
                    close_scope(entry["id"], exception_info)
                except Exception:
                    if active_scopes:
                        active_scopes.pop()
            failed_scopes.update(scope_path)
            command = yield (False, exc)


class _Session:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.node_outputs: dict[str, Any] = {}
        self.runner = _scope_runner(self)
        next(self.runner)

    def execute(self, operation_id: str, inputs: Any, config: dict[str, Any]) -> Any:
        response = self.runner.send({
            "operationId": operation_id,
            "inputs": inputs,
            "config": config,
        })
        success, value = response
        if not success:
            raise RuntimeError(str(value)) from value
        return value

    def dispose(self) -> None:
        try:
            self.runner.send({"action": "dispose"})
        except Exception:
            pass


def _release_session(session: _Session) -> None:
    session.dispose()
    for encoded in session.node_outputs.values():
        for token in _collect_refs(encoded):
            _REF_COUNTS[token] = max(0, _REF_COUNTS.get(token, 0) - 1)
    session.node_outputs.clear()
    _evict_unreferenced()


def _get_session(session_id: str) -> _Session:
    session = _SESSIONS.get(session_id)
    if session is None:
        session = _Session(session_id)
        _SESSIONS[session_id] = session
        while len(_SESSIONS) > _MAX_SESSIONS:
            _, expired = _SESSIONS.popitem(last=False)
            _release_session(expired)
    else:
        _SESSIONS.move_to_end(session_id)
    return session


def execute_operation(
    operation_id: str,
    inputs: Any = None,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Execute one Perigram operation and return JSON-safe values/opaque refs."""
    raw_config = dict(config or {})
    session_id = str(
        raw_config.get("executionId")
        or raw_config.get("runId")
        or raw_config.get("graphId")
        or "default"
    )
    session = _get_session(session_id)
    decoded_inputs = _decode(inputs or {}, session_id)
    decoded_config = _decode(raw_config, session_id)
    result = session.execute(operation_id, decoded_inputs, decoded_config)
    encoded = _encode(result, session_id)
    node_id = decoded_config.get("nodeId")
    if node_id:
        _set_node_output(session, str(node_id), encoded)
    return json.dumps(encoded, allow_nan=False, separators=(",", ":"))


def clear_runtime() -> None:
    """Release all Python values and unwind any still-open builder scopes."""
    for session in list(_SESSIONS.values()):
        _release_session(session)
    _SESSIONS.clear()
    _VALUE_STORE.clear()
    _OBJECT_REFS.clear()
    _REF_COUNTS.clear()
    _TOKEN_SESSIONS.clear()
