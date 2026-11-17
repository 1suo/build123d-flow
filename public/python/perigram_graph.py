"""Adapter from the Python translator's intermediate graph to Perigram graph JSON.

This module is deliberately kernel-free. It converts declarations and graph
structure only; native CAD values never enter persisted graph documents.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


GRAPH_FORMAT = "perigram.node-graph"
GRAPH_VERSION = 1
BUILD123D_PROVIDER = "build123d"
PYTHON_PROVIDER = "perigram.python"
OPERATION_VERSION = "1"


class PerigramGraphError(ValueError):
    """Raised when translator output cannot be represented without data loss."""


def semantic_descriptor(type_name: str | None) -> dict[str, Any]:
    """Map a discovered Python type identity to an open-world semantic descriptor."""
    name = str(type_name or "Any")
    primitives = {
        "Any": {"kind": "any"},
        "Number": {"kind": "primitive", "name": "number"},
        "Boolean": {"kind": "primitive", "name": "boolean"},
        "String": {"kind": "primitive", "name": "string"},
        "None": {"kind": "primitive", "name": "null"},
    }
    if name in primitives:
        return primitives[name]
    if name in {"List", "Tuple", "Map", "Tree"}:
        return {"kind": "structural", "name": name.lower()}
    return {"kind": "nominal", "provider": BUILD123D_PROVIDER, "typeId": name}


def transport_shape(socket: Mapping[str, Any]) -> str:
    """Derive transport shape independently from semantic type."""
    explicit = socket.get("data_structure") or socket.get("shape")
    if explicit in {"item", "list", "tree", "map", "tuple", "stream"}:
        return explicit
    access = socket.get("data_access")
    if access == "tree":
        return "tree"
    if access == "list":
        return "list"
    if socket.get("type") in {"List", "ShapeList"}:
        return "list"
    return "item"


def normalize_port(socket: Mapping[str, Any]) -> dict[str, Any]:
    name = str(socket.get("name") or socket.get("id") or "")
    if not name:
        raise PerigramGraphError("A discovered socket has no stable name")
    return {
        "id": name,
        "label": str(socket.get("label") or name),
        "shape": transport_shape(socket),
        "semantic": semantic_descriptor(socket.get("type")),
        "required": socket.get("required") is not False,
        **({"default": socket["default"]} if "default" in socket else {}),
        **({"description": str(socket["description"])} if socket.get("description") else {}),
    }


def normalize_node_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Publish one discovered operation as a Perigram provider-neutral definition."""
    operation_id = str(definition.get("id") or "")
    if not operation_id:
        raise PerigramGraphError("Discovered operation has no stable id")
    provider = str(definition.get("provider") or (
        PYTHON_PROVIDER if definition.get("source") == "python" else BUILD123D_PROVIDER
    ))
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", provider):
        raise PerigramGraphError(f"Invalid provider id: {provider}")
    inputs = [normalize_port(port) for port in definition.get("inputs", [])]
    outputs = [normalize_port(port) for port in definition.get("outputs", [])]
    return {
        "provider": provider,
        "id": operation_id,
        "version": OPERATION_VERSION,
        "label": str(definition.get("name") or operation_id),
        "description": str(definition.get("description") or ""),
        "palette": {
            "group": "build123d" if provider == BUILD123D_PROVIDER else "python",
            "label": "build123d" if provider == BUILD123D_PROVIDER else "Python",
            "keywords": ["cad", "geometry"] if provider == BUILD123D_PROVIDER else ["python", "language"],
        },
        "inputs": inputs,
        "outputs": outputs,
        "execution": {
            "kind": "pure",
            "runtime": "build123d-python",
            "targets": ["server"],
            "capabilities": [],
            "deterministic": False,
            "idempotent": True,
        },
    }


def translate_graph(source: str, translated: Mapping[str, Any]) -> dict[str, Any]:
    """Convert translator nodes/edges to the canonical Perigram graph document.

    Unknown constructs fail closed instead of silently dropping Python semantics.
    """
    nodes = translated.get("nodes")
    edges = translated.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise PerigramGraphError("Translator output must contain at least one node")
    if not isinstance(edges, list):
        raise PerigramGraphError("Translator output must contain an edge array")

    normalized_nodes = []
    for node in nodes:
        data = node.get("data") or {}
        definition = data.get("definition") or {}
        operation_id = str(definition.get("id") or "")
        if not operation_id:
            raise PerigramGraphError(f"Node {node.get('id')} has no discovered operation identity")
        provider = str(definition.get("provider") or (
            PYTHON_PROVIDER if definition.get("source") == "python" else BUILD123D_PROVIDER
        ))
        config = {
            "parameters": data.get("params") or {},
            "executionScopePath": data.get("execution_scope_path") or [],
        }
        if data.get("data_matching"):
            config["dataMatching"] = data["data_matching"]
        if data.get("python_loop_sources"):
            config["pythonLoopSources"] = data["python_loop_sources"]
        normalized_nodes.append({
            "id": str(node["id"]),
            "operation": {"provider": provider, "id": operation_id, "version": OPERATION_VERSION},
            "config": config,
            "position": node.get("position") or {"x": 0, "y": 0},
        })

    normalized_edges = []
    for index, edge in enumerate(edges, 1):
        source_node = str(edge.get("source") or "")
        target_node = str(edge.get("target") or "")
        source_port = str(edge.get("sourceHandle") or "result")
        target_port = str(edge.get("targetHandle") or "")
        if not all((source_node, target_node, target_port)):
            raise PerigramGraphError(f"Edge {edge.get('id') or index} is missing an endpoint or port")
        normalized_edges.append({
            "id": str(edge.get("id") or f"edge-{index}"),
            "source": {"node": source_node, "port": source_port},
            "target": {"node": target_node, "port": target_port},
        })

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return {
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


def provider_definitions(catalog: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert the discovered API catalog to provider-neutral node definitions."""
    return [normalize_node_definition(definition) for _, definition in sorted(catalog.items())]
