"""Markdown report describing the extracted knowledge graph."""

from __future__ import annotations

import os
from datetime import datetime

from .graph_builder import ENTITY_DESCRIPTIONS, RELATION_DESCRIPTIONS


def _table(rows: list[tuple], header: tuple) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def write_report(graph, config, result: dict, extractor, out_path: str) -> str:
    stats = result.get("graph", {})
    insights = result.get("insights", {})
    nodes = graph.entity_list()
    edges = graph.relation_list()
    by_id = {n["id"]: n for n in nodes}

    out: list[str] = []
    add = out.append

    add(f"# {config.title or config.project_name + ' 代码知识图谱'}")
    add("")
    add(f"生成时间：{datetime.now():%Y-%m-%d %H:%M} · 工程根目录：`{config.source_root}`")
    add("")
    add("## 一、图谱总览")
    add("")
    add(_table(
        [
            ("实体总数", stats.get("entities", 0)),
            ("关系总数", stats.get("relations", 0)),
            ("关系条数（含重复出现）", stats.get("total_relation_weight", 0)),
            ("跨文件函数调用", stats.get("cross_file_calls", 0)),
            ("宏展开点", result.get("expansions", 0)),
            ("宏展开点（有真实预处理文本）", insights.get("expansions_with_compiler_text", 0)),
            ("可展示宏展开结果的实体", insights.get("expanded_entities", 0)),
            ("解析的编译单元", (result.get("clang") or [{}]).__len__()),
            ("编译诊断（warning 及以上）", len(result.get("diagnostics", []))),
        ],
        ("指标", "数值"),
    ))
    add("")

    add("### 实体类型分布")
    add("")
    add(_table(
        [(k, v, ENTITY_DESCRIPTIONS.get(k, "")) for k, v in (stats.get("entity_types") or {}).items()],
        ("实体类型", "数量", "说明"),
    ))
    add("")
    add("### 关系类型分布")
    add("")
    add(_table(
        [(k, v, RELATION_DESCRIPTIONS.get(k, "")) for k, v in (stats.get("relation_types") or {}).items()],
        ("关系类型", "数量", "说明"),
    ))
    add("")

    add("## 二、构建过程")
    add("")
    add(_table(
        [
            ("clang（libclang）语义解析", f"{len(result.get('clang') or [])} 个编译单元"),
            ("tree-sitter 语法解析", f"{(result.get('tree_sitter') or {}).get('entities', 0)} 个节点交叉校验，"
                                    f"补充 {(result.get('tree_sitter') or {}).get('nodes_added', 0)} 个"),
            ("clang -E 真实预处理", f"{(result.get('preprocess') or {}).get('files', 0)} 个 .i 文件，驱动："
                                   f"`{(result.get('preprocess') or {}).get('driver', '未启用')}`"),
        ],
        ("阶段", "结果"),
    ))
    add("")

    diagnostics = result.get("diagnostics") or []
    if diagnostics:
        add("### 解析诊断")
        add("")
        add(_table(
            [(d.get("severity_name"), os.path.basename((d.get("location") or {}).get("file", "")),
              (d.get("location") or {}).get("line", ""), d.get("text", "")[:110])
             for d in diagnostics[:15]],
            ("级别", "文件", "行", "说明"),
        ))
        add("")

    add("## 三、结构与热点")
    add("")

    def list_table(title: str, rows: list[dict], columns=("名称", "类型", "文件", "数值")) -> None:
        if not rows:
            return
        add(f"### {title}")
        add("")
        add(_table(
            [(r.get("name"), r.get("type", ""), r.get("file", ""), r.get("value", "")) for r in rows],
            columns,
        ))
        add("")

    list_table("被调用最多的函数（枢纽）", insights.get("most_called", []))
    list_table("调用其他函数最多的函数", insights.get("call_hubs", []))
    list_table("使用最多的宏（展开热点）", insights.get("most_used_macros", []))
    list_table("访问最频繁的变量/字段（数据流热点）", insights.get("hottest_variables", []))

    unreferenced = insights.get("unreferenced_functions", [])
    if unreferenced:
        add(f"### 未被调用的函数（{insights.get('unreferenced_count', len(unreferenced))} 个，列出前 {len(unreferenced)}）")
        add("")
        add(_table(
            [(r["name"], r.get("file", ""), r.get("line", "")) for r in unreferenced],
            ("函数", "文件", "行"),
        ))
        add("")

    unused_macros = insights.get("macro_defined_but_unused", [])
    if unused_macros:
        add(f"### 定义但未被使用的宏（{len(unused_macros)} 个，最多列出 60）")
        add("")
        add(", ".join(f"`{m}`" for m in unused_macros))
        add("")

    dead = insights.get("dead_code", [])
    if dead:
        add(f"### 未被编译器编译的代码（{len(dead)} 个，来自 tree-sitter 文本解析）")
        add("")
        add("这些实体在源码文本中真实存在，但位于 `#if 0` 或未启用的条件分支里，clang 的 AST 中完全看不到它们。")
        add("")
        add(_table(
            [(d.get("name"), d.get("type"), d.get("file"), d.get("line")) for d in dead],
            ("名称", "类型", "文件", "行"),
        ))
        add("")

    external = insights.get("external_symbols", [])
    if external:
        add("### 工程外部符号（前 60 个）")
        add("")
        add(", ".join(f"`{name}`" for name in external))
        add("")

    deps = insights.get("file_dependencies", [])
    if deps:
        add(f"### 文件依赖（{len(deps)} 条）")
        add("")
        for dep in deps[:80]:
            add(f"- {dep}")
        add("")

    add("## 四、宏展开示例")
    add("")
    samples = [
        e for e in extractor.expansions
        if getattr(e, "expanded_compiler", "") and e.expanded
        and e.expanded.strip() != e.invocation.strip()
    ][:18]
    if samples:
        add(_table(
            [(f"`{e.file}:{e.line}`", f"`{e.invocation}`", e.expanded[:80], (e.expanded_compiler or "")[:80])
             for e in samples],
            ("位置", "源码中的写法", "本项目宏展开推导", "clang -E 实际输出"),
        ))
        add("")

    add("## 五、如何阅读图谱")
    add("")
    add("- `ckg.html`：双击打开（无需联网、无需服务器）。左侧可选预设视图：全景 / 文件模块 / 调用图 / 宏 / 类型 / 变量数据流。")
    add("- 点击节点查看详情：源码片段（原样）、宏展开结果、`clang -E` 预处理后的真实代码，以及全部上下游关系。")
    add("- 顶部搜索框可直接定位函数、变量或宏；双击节点进入“只看邻域”模式，用左侧“邻域深度”控制展开层数。")
    add("- 图谱里的 `USES_MACRO` 边记录了每处宏展开点；`s_ctx` 这类“看起来像变量、实际是宏”的写法，其数据流会指向真正的全局变量。")
    add("")
    add("### 输出文件")
    add("")
    add(_table(
        [
            ("entity.json", "全部实体（含 file/line/类型/度量）"),
            ("relation.json", "全部关系（含出现次数与聚合位置）"),
            ("graph.graphml", "可导入 Gephi / yEd / Neo4j 的标准图格式"),
            ("macro_expansions.json", "宏展开点，字段与 code_kg_with_tree-sitter 的 macro.json 兼容"),
            ("macro_expansions_detailed.json", "展开点 + 参数 + 本项目推导的展开文本 + clang 实际输出"),
            ("preprocessed/*.i", "clang -E 生成的真实预处理结果（宏全部展开后的代码）"),
            ("ckg.html", "交互式图谱可视化"),
            ("graph_stats.json / run_meta.json", "统计与运行元信息"),
        ],
        ("文件", "内容"),
    ))
    add("")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return out_path
