"""Read-only indexed access to a built code knowledge graph."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Iterable


class GraphStore:
    """Load entity/relation JSON once and provide reusable graph primitives."""

    def __init__(self, out_dir: str) -> None:
        self.out_dir = os.path.abspath(out_dir)
        self.entities = self._load("entity.json")
        self.relations = self._load("relation.json")
        self.by_id = {item["id"]: item for item in self.entities if item.get("id")}
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.by_type_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.out_edges: dict[str, list[dict]] = defaultdict(list)
        self.in_edges: dict[str, list[dict]] = defaultdict(list)
        for entity in self.entities:
            name = str(entity.get("name", "")).casefold()
            if name:
                self.by_name[name].append(entity)
                self.by_type_name[(entity.get("type", ""), name)].append(entity)
        for relation in self.relations:
            self.out_edges[relation.get("head", "")].append(relation)
            self.in_edges[relation.get("tail", "")].append(relation)
        for bucket in (self.by_name, self.by_type_name, self.out_edges, self.in_edges):
            for values in bucket.values():
                values.sort(key=lambda value: (
                    value.get("file", ""), value.get("line") or 0, value.get("id", "")
                ))

    def _load(self, name: str) -> list[dict]:
        path = os.path.join(self.out_dir, name)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON array")
        return data

    def search(self, query: str, *, entity_types: Iterable[str] | None = None,
               limit: int = 10) -> list[dict]:
        """Search exact names first, then stable substring matches."""
        needle = query.casefold().strip()
        allowed = _as_set(entity_types)
        exact = [
            item for item in self.by_name.get(needle, [])
            if not allowed or item.get("type") in allowed
        ]
        remaining = [
            item for item in self.entities
            if needle in str(item.get("name", "")).casefold()
            and item not in exact
            and (not allowed or item.get("type") in allowed)
        ]
        remaining.sort(key=lambda item: (
            0 if str(item.get("name", "")).casefold().startswith(needle) else 1,
            len(str(item.get("name", ""))),
            item.get("type", ""), item.get("file", ""), item.get("line") or 0,
        ))
        return (exact + remaining)[:max(0, limit)]

    def entity(self, entity_id: str) -> dict | None:
        return self.by_id.get(entity_id)

    def edges_from(self, entity_id: str, relation_types: Iterable[str] | None = None) -> list[dict]:
        return self._filter_edges(self.out_edges.get(entity_id, []), relation_types)

    def edges_to(self, entity_id: str, relation_types: Iterable[str] | None = None) -> list[dict]:
        return self._filter_edges(self.in_edges.get(entity_id, []), relation_types)

    @staticmethod
    def _filter_edges(edges: list[dict], relation_types: Iterable[str] | None) -> list[dict]:
        allowed = _as_set(relation_types)
        return [edge for edge in edges if not allowed or edge.get("type") in allowed]

    def neighbours(self, entity_id: str, *, depth: int = 1,
                   relation_types: Iterable[str] | None = None) -> dict[str, int]:
        distances = {entity_id: 0}
        frontier = [entity_id]
        allowed = _as_set(relation_types)
        for distance in range(1, max(0, depth) + 1):
            next_frontier = []
            for current in frontier:
                edges = self.out_edges.get(current, []) + self.in_edges.get(current, [])
                for edge in edges:
                    if allowed and edge.get("type") not in allowed:
                        continue
                    other = edge.get("tail") if edge.get("head") == current else edge.get("head")
                    if other and other not in distances:
                        distances[other] = distance
                        next_frontier.append(other)
            frontier = next_frontier
        return distances


def _as_set(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        return {values}
    return set(values)
