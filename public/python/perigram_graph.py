"""Adapter from the Python translator's intermediate graph to Perigram graph JSON.

This module is deliberately kernel-free. It converts declarations and graph
structure only; native CAD values never enter persisted graph documents.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


# These identifiers are exported by packages/core/src/graph.js in Perigram.
GRAPH_FORMAT = "perigram.graph"
GRAPH_VERSION = 1
BUILD123D_PROVIDER = "build123d"
PYTHON_PROVIDER = "perigram.python"
OPERATION_VERSION = "1"
SUPPORTED_SHAPES = {"item", "list", "tree", "map", "tuple", "stream"}
PROVIDER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


class PerigramGraphError(ValueError):
    """Raised when translator output cannot be represented without data loss."""


def _require_json(value: Any, context: str) -> None:
    """Reject values that Perigram's persisted JSON contracts cannot carry."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PerigramGraphError(f"{context} is not JSON-compatible: {exc}") from exc


def _provider_for_type(type_name: str, explicit_provider: str | None = None) -> str:
    if explicit_provider:
        provider = str(explicit_provider).lower()
    else:
        # Provider ownership comes from discovery metadata, never from a
        # type-name prefix. Unknown nominal types remain open-world Python.
        provider = PYTHON_PROVIDER
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise PerigramGraphError(f"Invalid semantic type provider: {provider}")
    return provider


PORT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _port_identifier(name: str) -> str:
    """Map Python parameter names to stable Perigram port identifiers."""
    original = str(name)
    if original.startswith("**"):
        candidate = "kwargs_" + original[2:]
    elif original.startswith("*"):
        candidate = "varargs_" + original[1:]
    else:
        candidate = original
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)
    if not candidate or not candidate[0].isalnum():
        candidate = "p_" + candidate
    if len(candidate) > 128:
        suffix = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
        candidate = candidate[:119] + "-" + suffix
    if not PORT_IDENTIFIER.fullmatch(candidate):
        raise PerigramGraphError(f"Cannot normalize Python port name: {original!r}")
    return candidate


def _normalize_loop_sources(sources: Any) -> list[dict[str, Any]]:
    normalized = []
    for source in sources or []:
        if not isinstance(source, Mapping):
            raise PerigramGraphError("Python loop source descriptors must be objects")
        item = dict(source)
        item["output_name"] = _port_identifier(
            source.get("output_name") or "result"
        )
        normalized.append(item)
    return normalized


def semantic_descriptor(
    type_name: str | None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Map type identity to semantic meaning, independently of data shape."""
    name = str(type_name or "Any")
    primitives = {
        "Any": {"kind": "any"},
        "Number": {"kind": "primitive", "name": "number"},
        "Boolean": {"kind": "primitive", "name": "boolean"},
        "String": {"kind": "primitive", "name": "string"},
        "None": {"kind": "primitive", "name": "null"},
        "int": {"kind": "primitive", "name": "number"},
        "float": {"kind": "primitive", "name": "number"},
        "bool": {"kind": "primitive", "name": "boolean"},
        "str": {"kind": "primitive", "name": "string"},
    }
    if name in primitives:
        return primitives[name]
    return {
        "kind": "nominal",
        "provider": _provider_for_type(name, provider),
        "typeId": name,
    }


def transport_shape(socket: Mapping[str, Any]) -> str:
    """Derive the outer transport shape from recursive data metadata."""
    if socket.get("data_representation") == "value":
        return "item"
    data_shape = socket.get("data_shape")
    if isinstance(data_shape, Mapping):
        kind = data_shape.get("kind")
        if kind == "map":
            return "map"
        if kind == "set":
            return "list"
        if kind == "union":
            return "stream"
        if kind in SUPPORTED_SHAPES:
            return str(kind)
    explicit = socket.get("data_structure") or socket.get("shape")
    if explicit == "dict":
        return "map"
    if explicit == "set":
        return "list"
    if explicit in SUPPORTED_SHAPES:
        return str(explicit)
    access = socket.get("data_access")
    if access in SUPPORTED_SHAPES:
        return str(access)
    return "item"


def _semantic_from_data_shape(
    shape: Any,
    fallback_type: str | None = None,
    fallback_provider: str | None = None,
) -> dict[str, Any]:
    """Preserve nested tuple/map/union semantics without conflating transport shape."""
    if not isinstance(shape, Mapping):
        return semantic_descriptor(fallback_type or "Any", fallback_provider)
    kind = shape.get("kind")
    if kind in {"list", "tree", "set"}:
        return _semantic_from_data_shape(
            shape.get("item"), fallback_type, fallback_provider
        )
    if kind == "map":
        return {
            "kind": "map",
            "key": _semantic_from_data_shape(shape.get("key")),
            "value": _semantic_from_data_shape(
                shape.get("value"), fallback_type, fallback_provider
            ),
        }
    if kind == "tuple":
        return {
            "kind": "tuple",
            "items": [
                _semantic_from_data_shape(item)
                for item in shape.get("items", [])
            ],
        }
    if kind == "union":
        return {
            "kind": "union",
            "options": [
                _semantic_from_data_shape(item)
                for item in shape.get("options", [])
            ],
        }
    if kind == "item":
        return semantic_descriptor(
            shape.get("type_id") or shape.get("type") or fallback_type or "Any",
            shape.get("type_provider") or fallback_provider,
        )
    return semantic_descriptor(fallback_type or "Any", fallback_provider)


def _semantic_for_socket(socket: Mapping[str, Any]) -> dict[str, Any]:
    fallback_type = socket.get("type_id") or socket.get("type")
    fallback_provider = socket.get("type_provider") or socket.get("provider")
    data_shape = socket.get("data_shape")
    if socket.get("data_representation") == "value":
        semantic = semantic_descriptor(fallback_type or "Any", fallback_provider)
        if isinstance(data_shape, Mapping) and data_shape.get("kind") == "union":
            representations = [
                _semantic_from_data_shape(option)
                for option in data_shape.get("options", [])
                if not (
                    isinstance(option, Mapping)
                    and option.get("kind") == "item"
                    and option.get("type_id") == socket.get("type_id")
                )
            ]
            if representations:
                semantic["representations"] = representations
        return semantic
    return _semantic_from_data_shape(data_shape, fallback_type, fallback_provider)


def normalize_port(socket: Mapping[str, Any]) -> dict[str, Any]:
    name = str(socket.get("name") or socket.get("id") or "")
    if not name:
        raise PerigramGraphError("A discovered socket has no stable name")
    result = {
        "id": _port_identifier(name),
        "label": str(socket.get("label") or name),
        "shape": transport_shape(socket),
        "semantic": _semantic_for_socket(socket),
        "required": socket.get("required") is not False,
    }
    if "default" in socket:
        result["default"] = socket["default"]
    if socket.get("description"):
        result["description"] = str(socket["description"])
    _require_json(result, f"Port {name}")
    return result


def _operation_provider(definition: Mapping[str, Any]) -> str:
    explicit = definition.get("provider")
    if explicit:
        return str(explicit).lower()
    source = definition.get("source")
    return PYTHON_PROVIDER if source in {"python", "flow"} else BUILD123D_PROVIDER


def normalize_node_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Publish one discovered operation as a Perigram node definition."""
    operation_id = str(definition.get("id") or "")
    if not operation_id:
        raise PerigramGraphError("Discovered operation has no stable id")
    provider = _operation_provider(definition)
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise PerigramGraphError(f"Invalid provider id: {provider}")
    raw_inputs = list(definition.get("inputs", []))
    raw_outputs = list(definition.get("outputs", []))
    inputs = [normalize_port(port) for port in raw_inputs]
    outputs = [normalize_port(port) for port in raw_outputs]
    for ports, direction in ((inputs, "inputs"), (outputs, "outputs")):
        identifiers = [port["id"] for port in ports]
        if len(identifiers) != len(set(identifiers)):
            raise PerigramGraphError(
                f"Python {direction} for {operation_id} collide after port-id normalization"
            )
    execution_kind = definition.get("execution_kind", "pure")
    if execution_kind not in {"pure", "read", "stateful", "proposal", "commit", "effect", "control"}:
        raise PerigramGraphError(f"Unsupported execution kind for {operation_id}: {execution_kind}")
    result = {
        "provider": provider,
        "id": operation_id,
        "version": str(definition.get("version") or OPERATION_VERSION),
        "label": str(definition.get("name") or operation_id),
        "inputs": inputs,
        "outputs": outputs,
        "execution": {
            "kind": execution_kind,
            # The plugin's JS runtime hosts Pyodide/OCP.wasm in-browser.
            # Native CAD values remain inside that runtime and cross the
            # provider boundary only as opaque/serialized outputs.
            "runtime": "build123d-pyodide",
            "targets": ["browser"],
            "capabilities": [],
            "deterministic": execution_kind == "pure",
            "idempotent": execution_kind == "pure",
        },
    }
    description = str(definition.get("description") or "")
    if description:
        result["description"] = description
    result["palette"] = {
        "group": "build123d" if provider == BUILD123D_PROVIDER else "python",
        "label": "build123d" if provider == BUILD123D_PROVIDER else "Python",
        "keywords": ["cad", "geometry"] if provider == BUILD123D_PROVIDER else ["python", "language"],
    }
    configuration = dict(definition.get("configuration") or {})
    input_aliases = {
        port["id"]: str(socket.get("name") or socket.get("id") or "")
        for socket, port in zip(raw_inputs, inputs)
        if port["id"] != str(socket.get("name") or socket.get("id") or "")
    }
    output_aliases = {
        port["id"]: str(socket.get("name") or socket.get("id") or "")
        for socket, port in zip(raw_outputs, outputs)
        if port["id"] != str(socket.get("name") or socket.get("id") or "")
    }
    if input_aliases or output_aliases:
        configuration["pythonPortNames"] = {
            "inputs": input_aliases,
            "outputs": output_aliases,
        }
    if configuration:
        result["configuration"] = configuration
    _require_json(result, f"Node definition {provider}.{operation_id}")
    return result


def translate_graph(source: str, translated: Mapping[str, Any]) -> dict[str, Any]:
    """Convert translator nodes/edges to Perigram's canonical graph document."""
    nodes = translated.get("nodes")
    edges = translated.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise PerigramGraphError("Translator output must contain at least one node")
    if not isinstance(edges, list):
        raise PerigramGraphError("Translator output must contain an edge array")

    source_nodes_by_id = {
        str(node.get("id")): node
        for node in nodes if isinstance(node, Mapping) and node.get("id")
    }
    incoming_sources: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        target_node = str(edge.get("target") or "")
        target_port = str(edge.get("targetHandle") or "")
        source_node = str(edge.get("source") or "")
        source_port = str(edge.get("sourceHandle") or "result")
        if not all((target_node, target_port, source_node)):
            continue
        source_data = source_nodes_by_id.get(source_node, {}).get("data") or {}
        incoming_sources.setdefault(target_node, {}).setdefault(
            _port_identifier(target_port), []
        ).append({
            "nodeId": source_node,
            "outputId": _port_identifier(source_port),
            "pythonLoopSources": _normalize_loop_sources(
                source_data.get("python_loop_sources") or []
            ),
        })

    normalized_nodes = []
    seen_node_ids = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            raise PerigramGraphError("Translator nodes must be objects")
        node_id = str(node.get("id") or "")
        if not node_id or node_id in seen_node_ids:
            raise PerigramGraphError(f"Missing or duplicate translator node id: {node_id!r}")
        seen_node_ids.add(node_id)
        data = node.get("data") or {}
        definition = data.get("definition") or {}
        operation_id = str(definition.get("id") or "")
        if not operation_id:
            raise PerigramGraphError(f"Node {node_id} has no discovered operation identity")
        provider = _operation_provider(definition)
        raw_params = data.get("params") or {}
        raw_inputs = list(definition.get("inputs", []))
        input_aliases = {
            _port_identifier(str(port.get("name") or port.get("id") or "")):
                str(port.get("name") or port.get("id") or "")
            for port in raw_inputs
            if _port_identifier(str(port.get("name") or port.get("id") or ""))
                != str(port.get("name") or port.get("id") or "")
        }
        config = {
            "parameters": {
                _port_identifier(name): value for name, value in raw_params.items()
            },
            "pythonPortNames": {"inputs": input_aliases},
            "executionScopePath": data.get("execution_scope_path") or [],
            # Perigram invokes operations individually. Carry the source
            # context-manager descriptors with each node so a stateful
            # runtime can preserve lexical builder scopes across invocations.
            "executionScopes": translated.get("execution_scopes") or [],
            "inputSources": incoming_sources.get(node_id, {}),
        }
        if data.get("data_matching"):
            config["dataMatching"] = data["data_matching"]
        if data.get("python_loop_sources"):
            config["pythonLoopSources"] = _normalize_loop_sources(data["python_loop_sources"])
        normalized_nodes.append({
            "id": node_id,
            "operation": {
                "provider": provider,
                "id": operation_id,
                "version": str(definition.get("version") or OPERATION_VERSION),
            },
            "config": config,
            "position": node.get("position") or {"x": 0, "y": 0},
        })

    normalized_edges = []
    seen_edge_ids = set()
    for index, edge in enumerate(edges, 1):
        if not isinstance(edge, Mapping):
            raise PerigramGraphError("Translator edges must be objects")
        source_node = str(edge.get("source") or "")
        target_node = str(edge.get("target") or "")
        source_port = _port_identifier(str(edge.get("sourceHandle") or "result"))
        target_port = _port_identifier(str(edge.get("targetHandle") or ""))
        edge_id = str(edge.get("id") or f"edge-{index}")
        if not all((source_node, target_node, source_port, target_port)):
            raise PerigramGraphError(f"Edge {edge_id} is missing an endpoint or port")
        if source_node not in seen_node_ids or target_node not in seen_node_ids:
            raise PerigramGraphError(f"Edge {edge_id} references a missing node")
        if edge_id in seen_edge_ids:
            raise PerigramGraphError(f"Duplicate translator edge id: {edge_id}")
        seen_edge_ids.add(edge_id)
        normalized_edges.append({
            "id": edge_id,
            "source": {"node": source_node, "port": source_port},
            "target": {"node": target_node, "port": target_port},
        })

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    result = {
        "format": GRAPH_FORMAT,
        "version": GRAPH_VERSION,
        "id": f"build123d-import-{digest}",
        "revision": f"source-{digest}",
        "name": "Imported build123d program",
        "description": "Translated from Python by the build123d Perigram provider.",
        "interface": {"inputs": [], "outputs": []},
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "metadata": {
            "source": {"language": "python", "sha256": digest},
            "translator": "build123d-flow",
            "executionScopes": translated.get("execution_scopes") or [],
        },
    }
    _require_json(result, "Perigram graph document")
    return result


def provider_definitions(catalog: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert the discovered API catalog to provider-neutral node definitions."""
    return [
        normalize_node_definition(definition)
        for _, definition in sorted(catalog.items())
    ]
