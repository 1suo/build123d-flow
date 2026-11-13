"""Kernel-neutral catalog indexing and receiver-aware member resolution.

The resolver consumes catalog records only. It knows nothing about build123d,
Python AST nodes, graph records, or any particular geometry kernel.
"""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class MemberResolution:
    definition_id: str | None
    candidates: tuple
    reason: str | None = None


class CatalogResolver:
    def __init__(self, definitions):
        self.definitions = definitions
        self.methods = defaultdict(list)
        self.properties = defaultdict(list)
        for definition_id, definition in definitions.items():
            inputs = definition.get("inputs", [])
            receiver = next(
                (item for item in inputs if item.get("name") in {"self", "cls"}),
                None,
            )
            if receiver is None:
                continue
            name = definition.get("name")
            if name:
                self.methods[name].append(definition_id)
            for marker in (".property.", ".field."):
                prefix, separator, member = definition_id.rpartition(marker)
                if separator and member:
                    self.properties[member].append(definition_id)

    @staticmethod
    def _receiver_score(socket, receiver_type):
        accepted = socket.get("variants") or [socket.get("type", "Any")]
        accepted = [item for item in accepted if item]
        if receiver_type in accepted:
            return 100
        if receiver_type == "Shape" and any(
            item in {"Shape", "Solid", "Part", "Compound"} for item in accepted
        ):
            return 80
        if "Any" in accepted:
            return 10
        return 0

    def resolve_member(self, member_name, receiver_type, *, property_only=False):
        """Resolve a method/property using one receiver compatibility policy."""
        pool = self.properties if property_only else self.methods
        ids = list(dict.fromkeys(pool.get(member_name, ())))
        # A property is catalog-addressed by semantic ID; methods by public name.
        if not property_only:
            ids.extend(
                item for item in self.properties.get(member_name, ())
                if item not in ids
            )
        scored = []
        for definition_id in ids:
            definition = self.definitions.get(definition_id, {})
            receiver = next(
                (item for item in definition.get("inputs", [])
                 if item.get("name") in {"self", "cls"}),
                None,
            )
            if receiver is None:
                continue
            score = self._receiver_score(receiver, receiver_type)
            if score:
                scored.append((score, definition_id))
        if not scored:
            return MemberResolution(None, ())
        best = max(score for score, _ in scored)
        winners = tuple(sorted(
            definition_id for score, definition_id in scored if score == best
        ))
        if len(winners) == 1:
            return MemberResolution(winners[0], winners)
        return MemberResolution(None, winners, "ambiguous receiver-compatible overloads")
