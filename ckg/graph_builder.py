"""In-memory knowledge graph with merge semantics and JSON export."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

RELATION_DESCRIPTIONS = {
    "CONTAINS": "文件/结构体/函数对成员的定义包含关系",
    "INCLUDES": "#include 引入关系",
    "CALLS": "函数调用（直接调用或已解析的函数指针调用）",
    "CALLS_PTR": "通过函数指针变量的间接调用",
    "CALLS_MACRO": "通过宏展开产生的调用",
    "ASSIGNED_TO": "赋值/绑定关系（函数指针绑定、字段赋值）",
    "HAS_PARAMETER": "函数形参归属",
    "HAS_VARIABLE": "函数局部变量归属",
    "HAS_MEMBER": "结构体/联合体字段归属",
    "TYPE_OF": "变量、字段、形参的结构体/枚举/typedef 类型",
    "RETURNS": "函数返回类型",
    "TYPEDEF_OF": "typedef 的底层类型",
    "READS": "函数读取变量/字段的值",
    "WRITES": "函数写入变量/字段的值",
    "USES_MACRO": "在代码中使用了某个宏（含展开结果）",
    "MACRO_DEPENDS_ON": "宏体内部引用另一个宏",
    "MACRO_ALIAS": "宏是另一个名字的别名",
    "GENERATES": "宏展开后产生了某个声明/定义",
    "DECLARES": "头文件对外声明某个实体",
    "INSTANCE_OF": "全局变量是某类型的实例",
}

ENTITY_DESCRIPTIONS = {
    "FILE": "源文件 / 头文件",
    "MACRO": "预处理宏（对象宏或函数宏）",
    "STRUCT": "结构体",
    "UNION": "联合体",
    "CLASS": "C++ 类 / 类模板",
    "ENUM": "枚举类型",
    "ENUM_CONST": "枚举常量",
    "TYPEDEF": "类型别名",
    "FUNCTION": "函数（含声明/定义）",
    "METHOD": "C++ 成员函数（含构造/析构/运算符）",
    "NAMESPACE": "C++ 命名空间",
    "VARIABLE": "全局变量",
    "LOCAL": "函数内部局部变量",
    "PARAMETER": "函数形参",
    "FIELD": "结构体/联合体字段",
    "EXTERNAL": "工程外部符号（驱动层 / 标准库 / 缺失头文件）",
}


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    # ------------------------------------------------------------------
    def node(self, node_id: str, kind: str, name: str, /, **attrs) -> str:
        existing = self.nodes.get(node_id)
        if existing is None:
            record = {"id": node_id, "type": kind, "name": name}
            record.update({k: v for k, v in attrs.items() if v not in (None, "", [])})
            self.nodes[node_id] = record
            return node_id
        if name and not existing.get("name"):
            existing["name"] = name
        for key, value in attrs.items():
            if value in (None, "", []):
                continue
            if key in ("is_definition", "external", "is_macro_generated"):
                # once true, stays true
                existing[key] = bool(existing.get(key)) or bool(value)
                continue
            if key == "expansion_count":
                existing[key] = int(existing.get(key, 0)) + int(value)
                continue
            if key in ("locations", "aliases", "tags", "generated_by_macro"):
                bucket = existing.setdefault(key, [])
                if isinstance(value, list):
                    for item in value:
                        if item not in bucket:
                            bucket.append(item)
                elif value not in bucket:
                    bucket.append(value)
                continue
            if key not in existing:
                existing[key] = value
        return node_id

    def edge(self, head: str, tail: str, rel: str, /, **attrs) -> None:
        if head == tail or not head or not tail:
            return
        key = (head, rel, tail)
        record = self.edges.get(key)
        if record is None:
            record = {"head": head, "tail": tail, "type": rel, "count": 1}
            record.update({k: v for k, v in attrs.items() if v not in (None, "", [])})
            self.edges[key] = record
            return
        record["count"] = record.get("count", 1) + 1
        for k, v in attrs.items():
            if v in (None, "", []):
                continue
            if k in ("sites", "arguments"):
                bucket = record.setdefault(k, [])
                items = v if isinstance(v, list) else [v]
                for item in items:
                    if item is bucket or item in bucket:
                        continue
                    bucket.append(item)
            elif k not in record:
                record[k] = v

    # ------------------------------------------------------------------
    def entity_list(self) -> list[dict]:
        return sorted(self.nodes.values(), key=lambda n: (n["type"], n["name"]))

    def merge(self, nodes: list[dict], edges: list[dict]) -> None:
        """Fold a sub-graph produced elsewhere (e.g. by a worker process)."""
        for record in nodes:
            node_id = record.get("id")
            if not node_id:
                continue
            attrs = {k: v for k, v in record.items() if k not in ("id", "type", "name")}
            self.node(node_id, record.get("type", "EXTERNAL"),
                      record.get("name", ""), **attrs)
        for record in edges:
            head, tail, rel = record.get("head"), record.get("tail"), record.get("type")
            if not (head and tail and rel):
                continue
            self.edge(head, tail, rel,
                      **{k: v for k, v in record.items() if k not in ("head", "tail", "type", "count")})

    def relation_list(self) -> list[dict]:
        return sorted(
            self.edges.values(),
            key=lambda e: (e["type"], e["head"], e["tail"]),
        )

    def degrees(self) -> dict[str, dict]:
        out = defaultdict(lambda: {"out": 0, "in": 0})
        for (head, _rel, tail), record in self.edges.items():
            out[head]["out"] += 1
            out[tail]["in"] += 1
        return out

    def stats(self) -> dict:
        deg = self.degrees()
        entity_counts = Counter(n["type"] for n in self.nodes.values())
        relation_counts = Counter(e["type"] for e in self.edges.values())
        cross_file = sum(
            1
            for (h, r, t) in self.edges
            if r == "CALLS"
            and self.nodes[h].get("file")
            and self.nodes[t].get("file")
            and self.nodes[h]["file"] != self.nodes[t]["file"]
        )
        return {
            "entities": len(self.nodes),
            "relations": len(self.edges),
            "entity_types": dict(entity_counts.most_common()),
            "relation_types": dict(relation_counts.most_common()),
            "cross_file_calls": cross_file,
            "total_relation_weight": sum(e.get("count", 1) for e in self.edges.values()),
            "max_out_degree": max((v["out"] for v in deg.values()), default=0),
            "max_in_degree": max((v["in"] for v in deg.values()), default=0),
        }

    # ------------------------------------------------------------------
    def dump(self, out_dir: str, *, indent: int = 1) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        entities = self.entity_list()
        relations = self.relation_list()
        deg = self.degrees()
        for record in entities:
            d = deg.get(record["id"], {"out": 0, "in": 0})
            record["in_degree"] = d["in"]
            record["out_degree"] = d["out"]
            record["degree"] = d["in"] + d["out"]

        with open(os.path.join(out_dir, "entity.json"), "w", encoding="utf-8") as fh:
            json.dump(entities, fh, ensure_ascii=False, indent=indent)
        with open(os.path.join(out_dir, "relation.json"), "w", encoding="utf-8") as fh:
            json.dump(relations, fh, ensure_ascii=False, indent=indent)
        stats = self.stats()
        with open(os.path.join(out_dir, "graph_stats.json"), "w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)
        return stats

    def to_graphml(self, path: str) -> None:
        def esc(value) -> str:
            text = str(value)
            return (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        with open(path, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            fh.write('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n')
            fh.write('  <graph id="ckg" edgedefault="directed">\n')
            for record in self.entity_list():
                fh.write(
                    '    <node id="%s"><data key="type">%s</data><data key="name">%s</data>'
                    "<data key=\"file\">%s</data></node>\n"
                    % (
                        esc(record["id"]),
                        esc(record["type"]),
                        esc(record["name"]),
                        esc(record.get("file", "")),
                    )
                )
            for i, record in enumerate(self.relation_list()):
                fh.write(
                    '    <edge id="e%d" source="%s" target="%s"><data key="type">%s</data></edge>\n'
                    % (i, esc(record["head"]), esc(record["tail"]), esc(record["type"]))
                )
            fh.write("  </graph>\n</graphml>\n")
