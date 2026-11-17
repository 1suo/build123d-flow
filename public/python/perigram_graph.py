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
    elif type_name.startswith("build123d."):
        provider = BUILD123D_PROVIDER
    else:
        # Unqualified/discovered Python types stay open-world. Do not assume
        # that every non-primitive type belongs to the CAD kernel.
        provider = PYTHON_PROVIDER
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise PerigramGraphError(f"Invalid semantic type provider: {provider}")
    return provider


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
    """Derive execution transport shape independently from semantic type."""
    explicit = socket.get("data_structure") or socket.get("shape")
    if explicit in SUPPORTED_SHAPES:
        return str(explicit)
    access = socket.get("data_access")
    if access in {"tree", "list"}:
        return str(access)
    # Legacy translator output can carry shape in the access declaration.
    return "item"


def normalize_port(socket: Mapping[str, Any]) -> dict[str, Any]:
    name = str(socket.get("name") or socket.get("id") or "")
    if not name:
        raise PerigramGraphError("A discovered socket has no stable name")
    result = {
        "id": name,
        "label": str(socket.get("label") or name),
        "shape": transport_shape(socket),
        "semantic": semantic_descriptor(
            socket.get("type_id") or socket.get("type"),
            socket.get("type_provider") or socket.get("provider"),
        ),
        "required": socket.get("required") is not False,
    }
    if "default" in socket:
        result["default"] = socket["default"]
    if socket.get("description"):
        result["description"] = str(socket["description"])
    _require_json(result, f"Port {name}")
    return result


def normalize_node_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Publish one discovered operation as a Perigram node definition."""
    operation_id = str(definition.get("id") or "")
    if not operation_id:
        raise PerigramGraphError("Discovered operation has no stable id")
    provider = str(definition.get("provider") or (
        PYTHON_PROVIDER if definition.get("source") == "python" else BUILD123D_PROVIDER
    )).lower()
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise PerigramGraphError(f"Invalid provider id: {provider}")
    inputs = [normalize_port(port) for port in definition.get("inputs", [])]
    outputs = [normalize_port(port) for port in definition.get("outputs", [])]
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
        provider = str(definition.get("provider") or (
            PYTHON_PROVIDER if definition.get("source") == "python" else BUILD123D_PROVIDER
        )).lower()
        config = {
            "parameters": data.get("params") or {},
            "executionScopePath": data.get("execution_scope_path") or [],
        }
        if data.get("data_matching"):
            config["dataMatching"] = data["data_matching"]
        if data.get("python_loop_sources"):
            config["pythonLoopSources"] = data["python_loop_sources"]
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
        source_port = str(edge.get("sourceHandle") or "result")
        target_port = str(edge.get("targetHandle") or "")
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
