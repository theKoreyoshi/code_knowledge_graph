"""Self-contained interactive HTML visualisation (no network, no build step)."""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "templates", "graph.html")
SCRIPT = os.path.join(HERE, "templates", "graph.js")

TYPE_COLORS = {
    "FILE": "#5ea0ff",
    "MACRO": "#f9a03f",
    "FUNCTION": "#38d39f",
    "VARIABLE": "#22d3ee",
    "LOCAL": "#7dd3fc",
    "PARAMETER": "#a5f3fc",
    "STRUCT": "#c084fc",
    "UNION": "#a78bfa",
    "ENUM": "#f472b6",
    "ENUM_CONST": "#fda4af",
    "TYPEDEF": "#fbbf24",
    "FIELD": "#94a3b8",
    "EXTERNAL": "#64748b",
}

TYPE_LABELS = {
    "FILE": "文件",
    "MACRO": "宏",
    "FUNCTION": "函数",
    "VARIABLE": "全局变量",
    "LOCAL": "局部变量",
    "PARAMETER": "形参",
    "STRUCT": "结构体",
    "UNION": "联合体",
    "ENUM": "枚举",
    "ENUM_CONST": "枚举常量",
    "TYPEDEF": "类型别名",
    "FIELD": "字段",
    "EXTERNAL": "外部符号",
}

REL_COLORS = {
    "CONTAINS": "#3d4d75",
    "INCLUDES": "#6b7fa8",
    "CALLS": "#38d39f",
    "CALLS_PTR": "#facc15",
    "CALLS_MACRO": "#fb923c",
    "READS": "#38bdf8",
    "WRITES": "#f43f5e",
    "ASSIGNED_TO": "#f97316",
    "HAS_PARAMETER": "#0891b2",
    "HAS_VARIABLE": "#0ea5e9",
    "HAS_MEMBER": "#a855f7",
    "TYPE_OF": "#8b5cf6",
    "RETURNS": "#22c55e",
    "TYPEDEF_OF": "#c4b5fd",
    "USES_MACRO": "#f59e0b",
    "MACRO_DEPENDS_ON": "#fbbf24",
    "MACRO_ALIAS": "#fde68a",
    "GENERATES": "#ef4444",
    "REFERENCES": "#64748b",
}

REL_LABELS = {
    "CONTAINS": "包含",
    "INCLUDES": "include",
    "CALLS": "调用",
    "CALLS_PTR": "指针调用",
    "CALLS_MACRO": "宏调用",
    "READS": "读取",
    "WRITES": "写入",
    "ASSIGNED_TO": "赋值",
    "HAS_PARAMETER": "形参",
    "HAS_VARIABLE": "局部变量",
    "HAS_MEMBER": "字段",
    "TYPE_OF": "类型",
    "RETURNS": "返回",
    "TYPEDEF_OF": "别名指向",
    "USES_MACRO": "使用宏",
    "MACRO_DEPENDS_ON": "宏依赖",
    "MACRO_ALIAS": "宏别名",
    "GENERATES": "宏生成",
    "REFERENCES": "引用",
}

ALL_TYPES = list(TYPE_LABELS)
ALL_RELS = list(REL_LABELS)

PRESETS = {
    "overview": {
        "label": "全景视图",
        "hint": "文件 / 函数 / 类型 / 宏 / 变量（默认隐去形参与局部变量）",
        "types": [t for t in ALL_TYPES if t not in ("PARAMETER", "LOCAL")],
        "rels": [r for r in ALL_RELS if r not in ("CONTAINS", "HAS_PARAMETER", "HAS_VARIABLE")],
    },
    "modules": {
        "label": "文件与模块依赖",
        "hint": "文件包含关系 + 每个文件的顶层实体",
        "types": ["FILE", "FUNCTION", "STRUCT", "UNION", "ENUM", "TYPEDEF", "MACRO", "VARIABLE", "EXTERNAL"],
        "rels": ["CONTAINS", "INCLUDES"],
    },
    "calls": {
        "label": "函数调用图",
        "hint": "只看函数之间的调用（含函数指针解析结果）",
        "types": ["FUNCTION"],
        "rels": ["CALLS", "CALLS_PTR", "CALLS_MACRO"],
    },
    "macros": {
        "label": "宏依赖与使用",
        "hint": "宏 → 宏依赖、代码中对宏的使用与展开点",
        "types": ["MACRO", "FUNCTION", "FILE"],
        "rels": ["USES_MACRO", "MACRO_DEPENDS_ON", "MACRO_ALIAS", "GENERATES", "CALLS_MACRO"],
    },
    "types": {
        "label": "类型与结构体",
        "hint": "结构体 / 联合体 / 枚举 / typedef 及其字段",
        "types": ["STRUCT", "UNION", "ENUM", "ENUM_CONST", "TYPEDEF", "FIELD"],
        "rels": ["HAS_MEMBER", "TYPE_OF", "TYPEDEF_OF", "CONTAINS"],
    },
    "dataflow": {
        "label": "变量数据流",
        "hint": "函数对全局变量 / 局部变量 / 字段的读写",
        "types": ["FUNCTION", "VARIABLE", "LOCAL", "PARAMETER", "FIELD", "EXTERNAL"],
        "rels": ["READS", "WRITES", "ASSIGNED_TO"],
    },
    "everything": {
        "label": "全部关系",
        "hint": "不做任何过滤（信息量最大，也最密集）",
        "types": ALL_TYPES,
        "rels": ALL_RELS,
    },
}


def _clip(value, limit: int) -> str:
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "…"


def _node_payload(node: dict) -> dict:
    keep = ("id", "type", "name", "file", "line", "degree", "in_degree", "out_degree",
            "external", "is_static", "is_pointer", "is_function_like", "signature",
            "return_type", "type_spelling", "storage_class", "scope", "metrics",
            "value", "expansion_count", "generated_by_macro", "params", "body",
            "expanded", "expanded_code", "snippet", "is_macro_generated", "path",
            "dead_code", "not_in_compiled_ast", "unmatched_by_clang",
            "only_in_tree_sitter", "engine", "is_definition", "category",
            "low_confidence", "auto_generated")
    payload = {k: node[k] for k in keep if k in node}
    for key in ("signature", "type_spelling", "return_type", "body", "expanded"):
        if key in payload:
            payload[key] = _clip(payload[key], 240)
    if "snippet" in payload:
        payload["snippet"] = _clip(payload["snippet"], 420)
    if "expanded_code" in payload:
        payload["expanded_code"] = _clip(payload["expanded_code"], 420)
    if isinstance(payload.get("generated_by_macro"), list):
        payload["generated_by_macro"] = payload["generated_by_macro"][:4]
    if isinstance(payload.get("params"), list):
        payload["params"] = payload["params"][:12]
    return payload


def write_visualisation(graph, config, out_path: str, result: dict | None = None) -> str:
    result = result or {}
    nodes = graph.entity_list()
    edges = graph.relation_list()

    payload = {
        "meta": {
            "title": config.title or f"{config.project_name} 代码知识图谱",
            "project": config.project_name,
            "entities": len(nodes),
            "relations": len(edges),
            "files": sum(1 for n in nodes if n["type"] == "FILE"),
            "macros": sum(1 for n in nodes if n["type"] == "MACRO"),
            "expansions": result.get("expansions", 0),
            "preprocessed_files": (result.get("preprocess") or {}).get("files", 0),
            "generator": "code-kg · clang %s + tree-sitter" % _clang_version(),
        },
        "nodes": [_node_payload(n) for n in nodes],
        "edges": [
            {
                "s": e["head"], "t": e["tail"], "type": e["type"], "count": e.get("count", 1),
                **({"via_macro": e["via_macro"]} if e.get("via_macro") else {}),
                **({"line": e["line"]} if e.get("line") else {}),
            }
            for e in edges
        ],
    }

    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        html = fh.read()
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        script = fh.read()

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_json = data_json.replace("</", "<\\/")

    script = (
        script.replace("__TYPE_COLORS__", json.dumps(TYPE_COLORS, ensure_ascii=False))
        .replace("__REL_COLORS__", json.dumps(REL_COLORS, ensure_ascii=False))
        .replace("__TYPE_LABELS__", json.dumps(TYPE_LABELS, ensure_ascii=False))
        .replace("__REL_LABELS__", json.dumps(REL_LABELS, ensure_ascii=False))
        .replace("__PRESETS__", json.dumps(PRESETS, ensure_ascii=False))
    )
    html = (
        html.replace("__TITLE__", payload["meta"]["title"])
        .replace("__DATA__", data_json)
        .replace("__SCRIPT__", script)
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def _clang_version() -> str:
    try:
        from .clang_capi import Clang

        return Clang.instance().version.split()[2]
    except Exception:
        return ""
