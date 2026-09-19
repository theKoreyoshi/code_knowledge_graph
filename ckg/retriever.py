"""AI-oriented retrieval and impact analysis over a built graph."""

from __future__ import annotations

import os
import json
from collections import defaultdict
from typing import Iterable

from .store import GraphStore

DEFAULT_CONTEXT_RELATIONS = (
    "CALLS", "CALLS_PTR", "CALLS_MACRO", "READS", "WRITES",
    "USES_MACRO", "TYPE_OF", "RETURNS", "INCLUDES", "CONTAINS",
    "HAS_PARAMETER", "HAS_VARIABLE", "HAS_MEMBER", "HAS_METHOD",
    "ASSIGNED_TO", "INHERITS",
)


class GraphRetriever:
    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def resolve(self, query: str, *, limit: int = 10,
                entity_types: Iterable[str] | None = None) -> list[dict]:
        return self.store.search(query, entity_types=entity_types, limit=limit)

    def context(self, query: str, *, depth: int = 1, max_files: int = 12,
                max_lines: int = 500, limit: int = 5,
                relation_types: Iterable[str] | None = None) -> dict:
        matches = self.resolve(query, limit=limit)
        if not matches:
            return {"query": query, "matches": [], "contexts": [], "warnings": [
                f"no entity matching {query!r}"
            ]}
        contexts = [
            self._one_context(entity, depth=depth, max_files=max_files,
                              max_lines=max_lines, relation_types=relation_types)
            for entity in matches
        ]
        return {"query": query, "matches": matches, "contexts": contexts,
                "warnings": self._warnings(contexts)}

    def impact(self, query: str, *, depth: int = 2, limit: int = 5) -> dict:
        matches = self.resolve(query, limit=limit)
        results = []
        for entity in matches:
            distances = self.store.neighbours(
                entity["id"], depth=depth, relation_types=DEFAULT_CONTEXT_RELATIONS
            )
            affected = [
                self.store.entity(node_id) | {"distance": distance}
                for node_id, distance in distances.items()
                if node_id != entity["id"] and self.store.entity(node_id)
            ]
            affected.sort(key=lambda item: (
                item["distance"], item.get("file", ""), item.get("line") or 0
            ))
            incoming = self.store.edges_to(entity["id"])
            outgoing = self.store.edges_from(entity["id"])
            files = sorted({
                item.get("file") for item in affected + [entity]
                if item.get("file")
            })
            score, reasons = self._risk(entity, incoming, outgoing, affected)
            results.append({
                "target": entity,
                "risk": {"level": self._risk_level(score), "score": score, "reasons": reasons},
                "direct_callers": self._related_nodes(incoming, "CALLS", incoming=True),
                "direct_callees": self._related_nodes(outgoing, "CALLS", incoming=False),
                "affected_entities": affected,
                "affected_files": files,
                "warnings": self._entity_warnings(entity),
            })
        return {"query": query, "matches": matches, "results": results}

    def _one_context(self, target: dict, *, depth: int, max_files: int,
                     max_lines: int, relation_types: Iterable[str] | None) -> dict:
        allowed = tuple(relation_types or DEFAULT_CONTEXT_RELATIONS)
        distances = self.store.neighbours(target["id"], depth=depth,
                                           relation_types=allowed)
        nodes = [self.store.entity(node_id) | {"distance": distance}
                 for node_id, distance in distances.items()
                 if self.store.entity(node_id)]
        nodes.sort(key=lambda item: (
            item["distance"], 0 if item["id"] == target["id"] else 1,
            item.get("file", ""), item.get("line") or 0
        ))
        files = []
        for node in nodes:
            file = node.get("file")
            if file and file not in files and len(files) < max_files:
                files.append(file)
        nodes = [node for node in nodes if not node.get("file") or node["file"] in files]
        node_ids = {node["id"] for node in nodes}
        relations = [
            relation for relation in self.store.relations
            if relation.get("head") in node_ids
            and relation.get("tail") in node_ids
            and relation.get("type") in allowed
        ]
        source = self._source_ranges(nodes, max_lines=max_lines)
        return {
            "target": target,
            "entities": nodes,
            "relations": relations,
            "source_ranges": source["ranges"],
            "source": source["source"],
            "source_lines": source["line_count"],
            "warnings": self._entity_warnings(target) + source["warnings"],
        }

    def _source_ranges(self, nodes: list[dict], *, max_lines: int) -> dict:
        grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for node in nodes:
            file = node.get("file")
            start = node.get("line")
            if not file or not isinstance(start, int):
                continue
            end = node.get("end_line") or start
            grouped[file].append((max(1, start), max(start, end)))
        ranges = []
        source = []
        used = 0
        warnings = []
        for file in sorted(grouped):
            intervals = _merge_intervals(grouped[file])
            for start, end in intervals:
                if used >= max_lines:
                    warnings.append("source line budget exhausted; some ranges omitted")
                    break
                take_end = min(end, start + max_lines - used - 1)
                ranges.append({
                    "file": file,
                    "path": self._absolute_source_path(file),
                    "start_line": start,
                    "end_line": take_end,
                })
                content, read_warning = self._read_source(
                    file, start, take_end
                )
                source.append({
                    "file": file,
                    "path": self._absolute_source_path(file),
                    "start_line": start,
                    "end_line": take_end,
                    "content": content,
                })
                if read_warning:
                    warnings.append(read_warning)
                used += take_end - start + 1
                if take_end < end:
                    warnings.append(f"source range truncated: {file}:{start}-{end}")
        return {
            "ranges": ranges, "source": source,
            "line_count": used, "warnings": warnings,
        }

    def _absolute_source_path(self, file: str) -> str:
        meta_path = os.path.join(self.store.out_dir, "run_meta.json")
        try:
            with open(meta_path, encoding="utf-8") as fh:
                root = json.load(fh).get("source_root", "")
            return os.path.normpath(os.path.join(root, file.replace("/", os.sep)))
        except (OSError, ValueError, TypeError):
            return file

    def _read_source(self, file: str, start: int, end: int) -> tuple[str, str | None]:
        path = self._absolute_source_path(file)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as exc:
            return "", f"source unavailable: {file} ({exc})"
        if start > len(lines):
            return "", f"source range outside file: {file}:{start}-{end}"
        return "\n".join(
            f"{number}: {lines[number - 1]}"
            for number in range(start, min(end, len(lines)) + 1)
        ), None

    def _related_nodes(self, edges: list[dict], relation_type: str, *,
                       incoming: bool) -> list[dict]:
        ids = []
        for edge in edges:
            if edge.get("type") != relation_type:
                continue
            node_id = edge.get("head") if incoming else edge.get("tail")
            if node_id and node_id not in ids:
                ids.append(node_id)
        return [self.store.entity(node_id) for node_id in ids if self.store.entity(node_id)]

    def _risk(self, target: dict, incoming: list[dict], outgoing: list[dict],
              affected: list[dict]) -> tuple[int, list[str]]:
        score = 0
        reasons = []
        if target.get("type") in {"STRUCT", "UNION", "CLASS", "TYPEDEF"}:
            score += 4
            reasons.append(f"修改类型实体 {target.get('type')} 可能影响布局或接口")
        if target.get("file", "").lower().endswith((".h", ".hpp", ".hh")):
            score += 3
            reasons.append("目标位于头文件，可能影响多个编译单元")
        call_in = sum(1 for edge in incoming if edge.get("type") in {"CALLS", "CALLS_PTR", "CALLS_MACRO"})
        if call_in >= 10:
            score += 3
            reasons.append(f"存在 {call_in} 个调用关系入边")
        elif call_in:
            score += 1
            reasons.append(f"存在 {call_in} 个调用关系入边")
        if any(edge.get("type") in {"CALLS_PTR", "ASSIGNED_TO"} for edge in incoming + outgoing):
            score += 3
            reasons.append("存在函数指针或赋值关系，静态影响范围可能不完整")
        if any(item.get("external") for item in affected):
            score += 2
            reasons.append("影响范围包含外部符号")
        return score, reasons

    @staticmethod
    def _risk_level(score: int) -> str:
        return "high" if score >= 7 else "medium" if score >= 3 else "low"

    def _entity_warnings(self, entity: dict) -> list[str]:
        warnings = []
        if entity.get("low_confidence") or entity.get("quality") in {"partial", "degraded"}:
            warnings.append("目标实体所在文件解析质量较低")
        if entity.get("external"):
            warnings.append("目标实体来自工程外部或 SDK")
        if entity.get("dead_code"):
            warnings.append("目标实体位于未参与当前编译的代码分支")
        return warnings

    def _warnings(self, contexts: list[dict]) -> list[str]:
        return sorted({warning for context in contexts for warning in context.get("warnings", [])})


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(item) for item in merged]
