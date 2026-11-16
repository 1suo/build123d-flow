"""Kernel-free access to versioned, discovery-generated API metadata.

This module deliberately imports no CAD package. Discovery may pass live class
objects to build_type_registry(), but consumers of TypeCatalog operate only on
plain JSON-compatible records.
"""
from __future__ import annotations

import enum
import inspect
from typing import Any, Mapping


SCHEMA_VERSION = 1


class CatalogError(ValueError):
    """The generated API catalog is incomplete or internally inconsistent."""


def _qualified_name(value: type) -> str:
    return "{}.{}".format(value.__module__, value.__qualname__)


def build_type_registry(classes, nodes):
    """Serialize every discovered public build123d class, independently of node eligibility."""
    registry = {}
    node_ids = tuple(nodes)
    for cls in sorted(set(classes), key=_qualified_name):
        if not inspect.isclass(cls):
            continue
        module = getattr(cls, "__module__", "")
        name = getattr(cls, "__name__", "")
        if not module.startswith("build123d") or not name or name.startswith("_"):
            continue
        identity = _qualified_name(cls)
        mro = [
            _qualified_name(owner)
            for owner in getattr(cls, "__mro__", ())
            if inspect.isclass(owner)
        ]
        bases = [_qualified_name(base) for base in getattr(cls, "__bases__", ())]
        try:
            is_enum = issubclass(cls, enum.Enum)
        except TypeError:
            is_enum = False
        enum_members = {}
        if is_enum:
            for member_name, member in cls.__members__.items():
                value = member.value
                if value is None or isinstance(value, (bool, int, float, str)):
                    enum_members[member_name] = value
                else:
                    enum_members[member_name] = repr(value)
        member_prefix = identity + "."
        constructor_ids = [
            node_id for node_id in node_ids
            if node_id == identity or node_id.startswith(identity + ".overload-")
        ]
        member_node_ids = [
            node_id for node_id in node_ids
            if node_id.startswith(member_prefix)
            and node_id not in constructor_ids
        ]
        registry[identity] = {
            "id": identity,
            "name": name,
            "qualname": cls.__qualname__,
            "module": module,
            "bases": bases,
            "mro": mro,
            "is_abstract": inspect.isabstract(cls),
            "is_enum": is_enum,
            "enum_members": enum_members,
            "annotations": {
                key: str(value)
                for key, value in vars(cls).get("__annotations__", {}).items()
            },
            "constructor_node_ids": constructor_ids,
            "member_node_ids": member_node_ids,
        }
    return registry


class TypeCatalog:
    """Resolve types, inheritance, and native operator nodes from serialized metadata."""

    def __init__(self, bundle: Mapping[str, Any]):
        if not isinstance(bundle, Mapping):
            raise CatalogError("Catalog bundle must be an object")
        version = bundle.get("schema_version")
        if version != SCHEMA_VERSION:
            raise CatalogError(
                "Unsupported catalog schema version {!r}; expected {}".format(
                    version, SCHEMA_VERSION
                )
            )
        raw_types = bundle.get("types")
        raw_nodes = bundle.get("nodes")
        if not isinstance(raw_types, list) or not isinstance(raw_nodes, list):
            raise CatalogError("Catalog bundle must contain type and node arrays")
        self.types = {}
        self.nodes = {}
        self._short_names = {}
        for record in raw_types:
            if not isinstance(record, Mapping) or not isinstance(record.get("id"), str):
                raise CatalogError("Every type record must have a stable id")
            identity = record["id"]
            if identity in self.types:
                raise CatalogError("Duplicate type identity: " + identity)
            if not isinstance(record.get("mro"), list) or identity not in record["mro"]:
                raise CatalogError("Type has invalid MRO metadata: " + identity)
            self.types[identity] = dict(record)
            self._short_names.setdefault(record.get("name"), []).append(identity)
        for record in raw_nodes:
            if not isinstance(record, Mapping) or not isinstance(record.get("id"), str):
                raise CatalogError("Every node record must have a stable id")
            identity = record["id"]
            if identity in self.nodes:
                raise CatalogError("Duplicate node identity: " + identity)
            self.nodes[identity] = dict(record)

    def resolve_type(self, identity: str) -> Mapping[str, Any]:
        """Resolve a fully qualified type id or an unambiguous short class name."""
        if identity in self.types:
            return self.types[identity]
        matches = self._short_names.get(identity, [])
        if len(matches) == 1:
            return self.types[matches[0]]
        if not matches:
            raise CatalogError("Unknown discovered type: " + identity)
        raise CatalogError(
            "Ambiguous type name {!r}: {}".format(identity, ", ".join(matches))
        )

    def is_subtype(self, candidate: str, expected: str) -> bool:
        """Use discovered Python MRO metadata, without importing the CAD kernel."""
        candidate_record = self.resolve_type(candidate)
        expected_record = self.resolve_type(expected)
        return expected_record["id"] in candidate_record["mro"]

    def resolve_operator(self, receiver_type: str, method: str) -> str:
        """Resolve a dunder node in the receiver's actual discovered MRO order."""
        record = self.resolve_type(receiver_type)
        for owner in record["mro"]:
            node_id = owner + ".operator." + method
            if node_id in self.nodes:
                return node_id
        raise CatalogError(
            "No discovered operator {} for receiver {}".format(method, receiver_type)
        )

    @classmethod
    def from_json(cls, payload: str) -> "TypeCatalog":
        import json
        return cls(json.loads(payload))
