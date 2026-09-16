"""泛化能力回归测试。

    python tools/validate.py [--out DIR] [--jobs N]

对形态不同的工程执行同一套零配置流程，然后对结果做断言。
用例 01-03 使用仓库自带示例；用例 04-05 在运行时**自动生成**，
因此这个脚本在任何机器上都是自包含的、可复现的。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, TOOL_ROOT)

from ckg.graph_builder import KnowledgeGraph  # noqa: E402
from ckg.pipeline import build_from_path  # noqa: E402

EXAMPLES = os.path.join(TOOL_ROOT, "examples")


# ----------------------------------------------------------------------
# 用例 04：带 compile_commands.json 的工程（运行时生成）
# ----------------------------------------------------------------------
def make_compile_db_project(target: str) -> str:
    src = os.path.join(EXAMPLES, "demo-c")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(src, target)
    entries = []
    for name in ("buffer.c", "registry.c", "main.c"):
        path = os.path.join(target, "src", name)
        entries.append({
            "directory": os.path.join(target, "src"),
            "file": path,
            "arguments": [
                "cc", "-c", path, "-o", "out.o",
                "-I", os.path.join(target, "include"),
                "-std=c11", "-DDEMO_FROM_COMPILE_DB=1",
            ],
        })
    with open(os.path.join(target, "compile_commands.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)
    return target


# ----------------------------------------------------------------------
# 用例 05：自动生成的大文件工程（运行时生成）
# ----------------------------------------------------------------------
LARGE_TEMPLATE_HEADER = """\
#include <stdint.h>

#define NODE_COUNT %(count)d
#define NODE_SCALE(v) ((v) * 3)

typedef struct node {
    int32_t value;
    struct node *next;
} node_t;

static node_t g_nodes[NODE_COUNT];
"""


def make_large_project(target: str, functions: int = 300) -> str:
    os.makedirs(os.path.join(target, "src"), exist_ok=True)
    lines = [LARGE_TEMPLATE_HEADER % {"count": functions}]
    for i in range(functions):
        call = f"    total += step_{i - 1}(value);\n" if i else ""
        lines.append(
            f"int32_t step_{i}(int32_t value)\n{{\n"
            f"    int32_t total = NODE_SCALE(value);\n{call}"
            f"    g_nodes[{i}].value = total;\n"
            f"    return total;\n}}\n"
        )
    with open(os.path.join(target, "src", "large.c"), "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    return target


# ----------------------------------------------------------------------
def build_cases(out_dir: str) -> list[dict]:
    return [
        {
            "name": "01-demo-c-cmake",
            "kind": "C11 + CMake",
            "path": os.path.join(EXAMPLES, "demo-c"),
            "expect_provider": "cmake",
            "expect": [
                ("node", "FUNCTION", "buffer_write"),
                ("node", "FUNCTION", "registry_dispatch"),
                ("node", "MACRO", "BUF_ALIGN4"),
                ("node", "STRUCT", "buffer"),
                ("node", "TYPEDEF", "buffer_t"),
                ("edge", "CALLS", "main", "registry_dispatch"),
                ("edge", "USES_MACRO", "main", "BUF_ALIGN4"),
                ("edge", "WRITES", "handle_echo", "s_errors"),
            ],
        },
        {
            "name": "02-demo-cpp-cmake",
            "kind": "C++17 + CMake（模板 / 继承 / STL）",
            "path": os.path.join(EXAMPLES, "demo-cpp"),
            "expect_provider": "cmake",
            "expect": [
                ("node", "CLASS", "Pipeline"),
                ("node", "CLASS", "MemorySink"),
                ("node", "NAMESPACE", "demo"),
                ("node", "MACRO", "DEMO_SCALE"),
                ("edge", "INHERITS", "MemorySink", "Sink"),
                ("edge", "HAS_METHOD", "Pipeline", "run"),
                ("edge", "CALLS", "main", "run"),
            ],
        },
        {
            "name": "03-embedded-autorepair",
            "kind": "嵌入式 C（IAR，无构建文件，缺厂商头文件）",
            "path": os.path.join(EXAMPLES, "embedded-iar"),
            "expect_provider": "generic",
            "expect": [
                ("node", "FUNCTION", "App_Run"),
                ("node", "MACRO", "app"),
                ("node", "STRUCT", "app_ctx_t"),
                ("edge", "CALLS", "App_PortInit", "Adc_Init"),
                ("macro_flow", "App_Run", "WRITES", "g_app_ctx", "app"),
                ("dead", "App_LegacyRun"),
                ("stubs", 4),
            ],
        },
        {
            "name": "04-compile-commands",
            "kind": "C + compile_commands.json（运行时生成）",
            "path": os.path.join(out_dir, "_generated", "compile-db-project"),
            "prepare": make_compile_db_project,
            "expect_provider": "compile_commands",
            "expect": [
                ("node", "FUNCTION", "buffer_write"),
                ("edge", "CALLS", "main", "registry_dispatch"),
                ("edge", "WRITES", "buffer_write", "tail"),
            ],
        },
        {
            "name": "05-large-generated",
            "kind": "自动生成的大型 C（300 个函数，无构建文件）",
            "path": os.path.join(out_dir, "_generated", "large-project"),
            "prepare": make_large_project,
            "expect_provider": "scan",
            "expect": [
                ("node", "FUNCTION", "step_0"),
                ("node", "FUNCTION", "step_299"),
                ("edge", "CALLS", "step_150", "step_149"),
                ("min_entities", 600),
            ],
        },
    ]


# ----------------------------------------------------------------------
def load_graph(out_dir: str) -> KnowledgeGraph:
    graph = KnowledgeGraph()
    with open(os.path.join(out_dir, "entity.json"), encoding="utf-8") as fh:
        nodes = json.load(fh)
    with open(os.path.join(out_dir, "relation.json"), encoding="utf-8") as fh:
        relations = json.load(fh)
    graph.merge(nodes, relations)
    return graph


def _candidates(by_name: dict, name: str) -> list[dict]:
    out: list[dict] = []
    for node_type in ("FUNCTION", "METHOD", "CLASS", "STRUCT", "NAMESPACE", "MACRO",
                      "VARIABLE", "LOCAL", "PARAMETER", "FIELD", "TYPEDEF", "ENUM",
                      "UNION", "ENUM_CONST", "EXTERNAL"):
        out += by_name.get((node_type, name), [])
    return out


def run_checks(graph: KnowledgeGraph, result: dict, expectations: list) -> list[dict]:
    by_name: dict[tuple, list[dict]] = {}
    for node in graph.nodes.values():
        by_name.setdefault((node["type"], node["name"]), []).append(node)
    checks: list[dict] = []

    for item in expectations:
        kind = item[0]
        if kind == "node":
            _k, node_type, name = item
            checks.append({"check": f"存在 {node_type} {name}",
                           "ok": bool(by_name.get((node_type, name)))})
        elif kind == "edge":
            _k, rel, head, tail = item
            hit = any((h["id"], rel, t["id"]) in graph.edges
                      for h in _candidates(by_name, head) for t in _candidates(by_name, tail))
            checks.append({"check": f"{head} -{rel}-> {tail}", "ok": hit})
        elif kind == "macro_flow":
            _k, head, rel, tail, macro = item
            hit = any(
                h["id"] == record["head"] and t["id"] == record["tail"]
                and record.get("via_macro") == macro
                for record in graph.edges.values()
                if record["type"] == rel
                for h in _candidates(by_name, head)
                for t in _candidates(by_name, tail)
            )
            checks.append({"check": f"{head} -{rel}-> {tail}（经宏 {macro}）", "ok": hit})
        elif kind == "dead":
            _k, name = item
            hit = any(node.get("dead_code") for node in _candidates(by_name, name))
            checks.append({"check": f"识别出未编译代码 {name}", "ok": hit})
        elif kind == "stubs":
            _k, minimum = item
            generated = (result.get("auto_repair") or {}).get("generated") or {}
            total = generated.get("headers", 0)
            checks.append({"check": f"自动生成缺失头文件桩 ≥ {minimum}（实际 {total}）",
                           "ok": total >= minimum})
        elif kind == "min_entities":
            _k, minimum = item
            total = len(graph.nodes)
            checks.append({"check": f"实体数 ≥ {minimum}（实际 {total}）",
                           "ok": total >= minimum})
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="code-kg generalization tests")
    parser.add_argument("--out", default=os.path.join(EXAMPLES, "validation"))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--keep", action="store_true", help="保留生成用例的目录")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    cases = build_cases(os.path.abspath(args.out))
    rows: list[dict] = []

    for case in cases:
        print("=" * 74)
        print(f"[{case['name']}] {case['kind']}")
        path = case["path"]
        if "prepare" in case:
            path = case["prepare"](path)
            print(f"  generated: {path}")
        elif not os.path.isdir(path):
            print("  skipped: 目录不存在")
            continue

        out_dir = os.path.join(args.out, case["name"])
        # always start from a clean output directory: a stale `_auto_shim/`
        # from a previous run would hide what the auto-repair actually does
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        logs: list[str] = []
        started = time.time()
        result = build_from_path(path, output_dir=out_dir, jobs=args.jobs,
                                 log=lambda message="": logs.append(message))
        elapsed = time.time() - started

        provider = result.get("build_provider", "?")
        stats = result.get("graph") or {}
        graph = load_graph(out_dir)
        checks = run_checks(graph, result, case["expect"])
        passed = sum(1 for check in checks if check["ok"])
        rows.append({
            "name": case["name"],
            "kind": case["kind"],
            "provider": provider,
            "provider_ok": provider == case["expect_provider"],
            "entities": stats.get("entities"),
            "relations": stats.get("relations"),
            "errors": (result.get("diagnostics_summary") or {}).get("errors"),
            "degraded": (result.get("parse_quality") or {}).get("degraded_count", 0),
            "seconds": round(elapsed, 1),
            "checks": checks,
            "passed": passed,
            "total": len(checks),
        })
        print(f"  provider={provider} entities={rows[-1]['entities']} "
              f"relations={rows[-1]['relations']} errors={rows[-1]['errors']} "
              f"checks={passed}/{len(checks)} ({elapsed:.1f}s)")
        with open(os.path.join(out_dir, "build.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(logs))

    markdown = ["# 泛化能力验证报告", "",
                "由 `python tools/validate.py` 自动生成。", "",
                "| 用例 | 形态 | 自动识别 | 实体 | 关系 | 解析错误 | 断言 | 耗时 |",
                "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        mark = "" if row["provider_ok"] else " ⚠"
        markdown.append(
            f"| {row['name']} | {row['kind']} | {row['provider']}{mark} | {row['entities']}"
            f" | {row['relations']} | {row['errors']} | {row['passed']}/{row['total']}"
            f" | {row['seconds']}s |"
        )
    markdown.append("")
    for row in rows:
        markdown += [f"## {row['name']}", "",
                     f"- 形态：{row['kind']}",
                     f"- 自动识别：`{row['provider']}` {'✅' if row['provider_ok'] else '❌'}",
                     f"- 图谱：{row['entities']} 实体 / {row['relations']} 关系",
                     f"- 解析质量：{row['errors']} 个错误，{row['degraded']} 个文件降级",
                     "- 断言："]
        for check in row["checks"]:
            markdown.append(f"    - {'✅' if check['ok'] else '❌'} {check['check']}")
        markdown.append("")
    with open(os.path.join(args.out, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(markdown))

    print("=" * 74)
    print(f"报告: {os.path.join(args.out, 'REPORT.md')}")
    failed = [r for r in rows if not r["provider_ok"] or r["passed"] != r["total"]]
    if failed:
        print(f"失败 {len(failed)} 个用例: {[r['name'] for r in failed]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
