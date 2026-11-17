"""Kernel-free access to versioned, discovery-generated API metadata.

This module deliberately imports no CAD package. Discovery may pass live class
objects to build_type_registry(), but consumers of TypeCatalog operate only on
plain JSON-compatible records.
"""
from __future__ import annotations

import enum
import inspect
import collections.abc
import math
import typing
from typing import Any, Mapping, get_args


SCHEMA_VERSION = 7


class CatalogError(ValueError):
    """The generated API catalog is incomplete or internally inconsistent."""


def _qualified_name(value: type) -> str:
    return "{}.{}".format(value.__module__, value.__name__)


def _serialize_discovered_value(value, depth=0):
    """Serialize safe public value objects by their observable data contract."""
    if depth > 5:
        return None
    if isinstance(value, enum.Enum):
        return {
            "__flow_enum__": [
                value.__class__.__module__,
                value.__class__.__name__,
                value.name,
            ]
        }
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        items = [_serialize_discovered_value(item, depth + 1) for item in value]
        if any(item is None and original is not None for item, original in zip(items, value)):
            return None
        return items
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            serialized = _serialize_discovered_value(item, depth + 1)
            if serialized is None and item is not None:
                return None
            result[str(key)] = serialized
        return result
    cls = type(value)
    if getattr(cls, "__module__", "").split(".")[0] != "build123d":
        return None
    type_name = getattr(cls, "__name__", "")
    try:
        if all(hasattr(value, name) for name in ("origin", "x_dir", "z_dir")):
            return {
                "__flow_object__": type_name,
                "origin": _serialize_discovered_value(value.origin, depth + 1),
                "x_dir": _serialize_discovered_value(value.x_dir, depth + 1),
                "z_dir": _serialize_discovered_value(value.z_dir, depth + 1),
            }
        if all(hasattr(value, name) for name in ("position", "direction")):
            return {
                "__flow_object__": type_name,
                "args": [
                    _serialize_discovered_value(value.position, depth + 1),
                    _serialize_discovered_value(value.direction, depth + 1),
                ],
                "kwargs": {},
            }
        to_tuple = getattr(value, "to_tuple", None)
        if callable(to_tuple):
            return {
                "__flow_object__": type_name,
                "value": _serialize_discovered_value(list(to_tuple()), depth + 1),
            }
        iterator = iter(value)
        items = [_serialize_discovered_value(item, depth + 1) for item in iterator]
        if items and all(item is not None for item in items):
            return {"__flow_object__": type_name, "value": items}
    except Exception:
        return None
    return None


def build_symbol_registry(module, modules=()):
    """Discover public package exports and module-level APIs without name allowlists."""
    symbols = {}
    candidates = [module] + [item for item in modules if item is not module]
    for candidate_module in candidates:
        for exported_name, value in vars(candidate_module).items():
            if exported_name.startswith("_"):
                continue
            module_name = getattr(value, "__module__", "")
            if inspect.isclass(value) and module_name.split(".")[0] == "build123d":
                symbols.setdefault(exported_name, {
                    "kind": "type",
                    "type_id": _qualified_name(value),
                })
                continue
            if callable(value) and module_name.split(".")[0] == "build123d":
                symbols.setdefault(exported_name, {
                    "kind": "callable",
                    "id": module_name + "." + getattr(value, "__name__", exported_name),
                })
                continue
            serialized = _serialize_discovered_value(value)
            if serialized is not None:
                symbols.setdefault(exported_name, {
                    "kind": "value",
                    "value": serialized,
                })
    for exported_name in getattr(module, "__all__", ()):
        if exported_name in symbols:
            continue
        value = getattr(module, exported_name, None)
        module_name = getattr(value, "__module__", "")
        if callable(value) and module_name.split(".")[0] == "build123d":
            symbols[exported_name] = {
                "kind": "callable",
                "id": module_name + "." + getattr(value, "__name__", exported_name),
            }
        elif inspect.isclass(value) and module_name.split(".")[0] == "build123d":
            symbols[exported_name] = {
                "kind": "type",
                "type_id": _qualified_name(value),
            }
        else:
            serialized = _serialize_discovered_value(value)
            if serialized is not None:
                symbols[exported_name] = {"kind": "value", "value": serialized}
    return symbols

def build_type_registry(classes, nodes, geometry_base=None):
    """Serialize every discovered public build123d class, independently of node eligibility."""
    registry = {}
    if geometry_base is None:
        geometry_base = next(
            (candidate for candidate in classes
             if inspect.isclass(candidate)
             and getattr(candidate, "__module__", "").split(".")[0] == "build123d"
             and getattr(candidate, "__name__", "") == "Shape"),
            None,
        )
    node_ids = tuple(nodes)
    build123d_classes_by_name = {}
    for candidate in classes:
        if inspect.isclass(candidate) and getattr(candidate, "__module__", "").split(".")[0] == "build123d":
            build123d_classes_by_name.setdefault(candidate.__name__, candidate)
    for cls in sorted(set(classes), key=_qualified_name):
        if not inspect.isclass(cls):
            continue
        module = getattr(cls, "__module__", "")
        name = getattr(cls, "__name__", "")
        if module.split(".")[0] != "build123d" or not name or name.startswith("_"):
            continue
        identity = _qualified_name(cls)
        mro = [
            _qualified_name(owner)
            for owner in getattr(cls, "__mro__", ())
            if inspect.isclass(owner)
        ]
        bases = [_qualified_name(base) for base in getattr(cls, "__bases__", ())]
        declared_mro = set(getattr(cls, "__mro__", ()))
        if dict in declared_mro or collections.abc.Mapping in declared_mro:
            collection_kind = "dict"
        elif set in declared_mro or frozenset in declared_mro or collections.abc.Set in declared_mro:
            collection_kind = "set"
        elif tuple in declared_mro:
            collection_kind = "tuple"
        elif (
            list in declared_mro
            or collections.abc.Sequence in declared_mro
            or collections.abc.MutableSequence in declared_mro
            or collections.abc.Collection in declared_mro
            or collections.abc.Iterable in declared_mro
        ):
            collection_kind = "list"
        else:
            collection_kind = None
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
        class_values = {}
        if not is_enum:
            # Read public class attributes through the class so class-property
            # descriptors (for example Plane.XY) are discovered as values.
            # Ordinary instance properties return a descriptor on class access
            # and are rejected by the serializer; methods remain callable.
            # Class properties may live on the metaclass rather than in
            # dir(cls) (Plane.XY is a PlaneMeta property). Discover both
            # namespaces, then resolve each name through the actual class.
            member_names = set(dir(cls)) | set(dir(type(cls)))
            for member_name in sorted(member_names):
                if member_name.startswith("_"):
                    continue
                try:
                    class_value = getattr(cls, member_name)
                except Exception:
                    continue
                if callable(class_value):
                    continue
                serialized = _serialize_discovered_value(class_value)
                if serialized is not None:
                    class_values[member_name] = serialized
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
        supports_context_manager = any(
            "__enter__" in vars(owner) and "__exit__" in vars(owner)
            for owner in getattr(cls, "__mro__", ())
        )
        context_result_attribute = None
        context_result_type = None
        if supports_context_manager and name.startswith("Build") and len(name) > len("Build"):
            candidate_attribute = name[len("Build"):].lower()
            for owner_id in mro:
                property_id = owner_id + ".property." + candidate_attribute
                property_definition = nodes.get(property_id)
                if not property_definition:
                    continue
                outputs = property_definition.get("outputs", [])
                if not outputs:
                    continue
                output_type = outputs[0].get("type")
                output_class = build123d_classes_by_name.get(output_type)
                if output_class is not None and inspect.isclass(geometry_base):
                    try:
                        is_geometry_result = issubclass(output_class, geometry_base)
                    except TypeError:
                        is_geometry_result = False
                    if is_geometry_result:
                        context_result_attribute = candidate_attribute
                        context_result_type = output_type
                        break
        iterator = getattr(cls, "__iter__", None)
        iterable_item_type = None
        if callable(iterator):
            try:
                iterator_signature = inspect.signature(iterator)
                iterator_hints = typing.get_type_hints(iterator)
                iterator_return = iterator_hints.get(
                    "return", iterator_signature.return_annotation
                )
                item_candidates = get_args(iterator_return)
                if item_candidates:
                    candidate = item_candidates[0]
                    if inspect.isclass(candidate) and getattr(candidate, "__module__", "").startswith("build123d"):
                        iterable_item_type = candidate.__name__
            except (TypeError, ValueError, NameError):
                pass
        if iterable_item_type is None:
            # Generic collection bases often declare their item class in
            # __orig_bases__ even when __iter__ only exposes an unresolved T.
            for owner in getattr(cls, "__mro__", (cls,)):
                for declared_base in vars(owner).get("__orig_bases__", ()):
                    candidates = get_args(declared_base)
                    candidate = next((
                        item for item in candidates
                        if inspect.isclass(item)
                        and getattr(item, "__module__", "").split(".")[0] == "build123d"
                    ), None)
                    if candidate is not None:
                        iterable_item_type = candidate.__name__
                        break
                if iterable_item_type is not None:
                    break
        registry[identity] = {
            "id": identity,
            "name": name,
            "qualname": cls.__qualname__,
            "module": module,
            "bases": bases,
            "mro": mro,
            "is_abstract": inspect.isabstract(cls),
            "is_geometry": bool(
                inspect.isclass(geometry_base)
                and issubclass(cls, geometry_base)
            ),
            "socket_type": name,
            "supports_context_manager": supports_context_manager,
            "context_result_attribute": context_result_attribute,
            "context_result_type": context_result_type,
            "supports_iteration": callable(iterator),
            "collection_kind": collection_kind,
            "iterable_item_type": iterable_item_type,
            "is_enum": is_enum,
            "enum_members": enum_members,
            "class_values": class_values,
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

    SCHEMA_VERSION = SCHEMA_VERSION

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
        raw_symbols = bundle.get("symbols", {})
        if not isinstance(raw_types, list) or not isinstance(raw_nodes, list):
            raise CatalogError("Catalog bundle must contain type and node arrays")
        if not isinstance(raw_symbols, Mapping):
            raise CatalogError("Catalog symbols must be an object")
        self.symbols = dict(raw_symbols)
        self.types = {}
        self.nodes = {}
        self._short_names = {}
        for record in raw_types:
            if not isinstance(record, Mapping) or not isinstance(record.get("id"), str):
                raise CatalogError("Every type record must have a stable id")
            identity = record["id"]
            if identity in self.types:
                raise CatalogError("Duplicate type identity: " + identity)
            mro = record.get("mro")
            if (
                not isinstance(mro, list)
                or not mro
                or mro[0] != identity
                or len(mro) != len(set(mro))
            ):
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
        for identity, record in self.types.items():
            referenced_nodes = (
                list(record.get("constructor_node_ids", []))
                + list(record.get("member_node_ids", []))
            )
            missing = [node_id for node_id in referenced_nodes if node_id not in self.nodes]
            if missing:
                raise CatalogError(
                    "Type {} references missing graph nodes: {}".format(
                        identity, ", ".join(missing)
                    )
                )

    def resolve_symbol(self, name: str) -> Mapping[str, Any]:
        """Resolve a public root-package export, including aliases and constants."""
        symbol = self.symbols.get(name)
        if symbol is not None:
            return symbol
        try:
            record = self.resolve_type(name)
        except CatalogError:
            raise CatalogError("Unknown discovered build123d symbol: " + name)
        return {"kind": "type", "type_id": record["id"]}

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

    def resolve_enum_member(self, enum_type: str, member_name: str) -> dict:
        """Return a serializable enum reference using discovered enum metadata."""
        record = self.resolve_type(enum_type)
        if not record.get("is_enum"):
            raise CatalogError("Discovered type is not an enum: " + record["id"])
        if member_name not in record.get("enum_members", {}):
            raise CatalogError(
                "Unknown enum member {}.{}".format(record["id"], member_name)
            )
        return {
            "__flow_enum__": [record["module"], record["name"], member_name]
        }

    def resolve_class_value(self, type_name: str, member_name: str) -> Any:
        """Return a serialized public class-level value discovered from the kernel."""
        record = self.resolve_type(type_name)
        values = record.get("class_values", {})
        if member_name not in values:
            raise CatalogError(
                "Unknown discovered class value {}.{}".format(record["id"], member_name)
            )
        return values[member_name]

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
