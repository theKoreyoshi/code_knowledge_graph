"""End-to-end build: detect -> repair -> extract -> expand -> export -> show."""

from __future__ import annotations

import json
import os
import time

from . import autoconfig, compile_db
from .argbuilder import ArgBuilder
from .clang_capi import Clang
from .config import ProjectConfig
from .extract_clang import ClangExtractor, finalize_macros
from .extract_treesitter import TreeSitterExtractor
from .graph_builder import KnowledgeGraph
from .parallel_extract import extract_parallel
from .preprocess import Preprocessor
from .report import write_report
from .shimgen import AutoRepair
from .toolchain import Toolchain
from .visualize import write_visualisation


class Pipeline:
    def __init__(self, config: ProjectConfig, log=print) -> None:
        self.config = config
        self.log = log
        self.result: dict = {"project": config.project_name, "source_root": config.source_root}

    # ------------------------------------------------------------------
    def run(
        self,
        jobs: int = 0,
        engine: str = "clang+treesitter",
        use_cache: bool = True,
        detect_only: bool = False,
    ) -> dict:
        config = self.config
        out_dir = config.output_dir
        os.makedirs(out_dir, exist_ok=True)
        jobs = jobs or config.jobs or 0

        clang = Clang.instance()
        self.result["toolchain_env"] = {
            "libclang": clang.version.splitlines()[0],
        }
        self.log(f"  libclang: {clang.version.splitlines()[0]}")

        toolchain = Toolchain(config, log=self.log)
        toolchain.write_compat_header(out_dir, clang_major=_clang_major(clang))
        self.result["toolchain"] = {
            "driver": toolchain.describe(),
            "available": toolchain.available,
        }
        self.log(f"  compiler driver: {toolchain.describe()}")

        sources, headers = config.discover_sources()
        self.log(f"  sources: {len(sources)} translation units, {len(headers)} headers")
        self.result["sources"] = {"translation_units": len(sources), "headers": len(headers)}
        if not sources and not headers:
            self.result["error"] = "no source files matched the configuration"
            return self.result

        database = compile_db.load(config.compile_commands)
        if database:
            self.log(f"  compile_commands.json: {len(database)} entries")
        self.result["compile_commands"] = {
            "path": config.compile_commands,
            "entries": len(database),
        }

        repair = AutoRepair(config, out_dir, log=self.log) if config.auto_repair else None
        if repair is not None:
            config._auto_shim_dir = repair.include_dir
        args_builder = ArgBuilder(config, toolchain, repair=repair, compile_db=database)

        if repair is not None:
            started = time.time()
            repair_info = repair.run(clang, sources + headers, args_builder.for_file)
            repair_info["seconds"] = round(time.time() - started, 1)
            self.result["auto_repair"] = repair_info
            self.log(f"  auto-repair: {repair_info.get('generated')}")

        args_builder = ArgBuilder(config, toolchain, repair=repair, compile_db=database)
        started = time.time()
        merged = extract_parallel(config, sources, args_builder, jobs=jobs, log=self.log)
        graph: KnowledgeGraph = merged["graph"]
        self.log(f"  clang pass: {time.time() - started:.1f}s "
                 f"({len(sources)} units, {jobs or 'auto'} workers)")

        orphans = [
            header for header in headers
            if os.path.normcase(os.path.normpath(header)) not in merged["touched_files"]
        ]
        if orphans:
            self.log(f"  orphan headers not reachable from any unit: {len(orphans)}")
            orphan_result = extract_parallel(config, orphans, args_builder, jobs=1, log=self.log)
            graph.merge(
                list(orphan_result["graph"].nodes.values()),
                list(orphan_result["graph"].edges.values()),
            )
            for name, definition in orphan_result["macro_defs"].items():
                merged["macro_defs"].setdefault(name, definition)
            for name, defs in orphan_result["macro_locations"].items():
                merged["macro_locations"].setdefault(name, []).extend(defs)
            merged["expansions"].extend(orphan_result["expansions"])
            for var, targets in orphan_result["pointer_assignments"].items():
                merged["pointer_assignments"].setdefault(var, set()).update(targets)
            merged["diagnostics"].extend(orphan_result["diagnostics"])
            merged["files_parsed"].extend(orphan_result["files_parsed"])

        expansions = finalize_macros(
            graph,
            merged["macro_defs"],
            merged["macro_locations"],
            merged["expansions"],
            config,
            merged["pointer_assignments"],
            self.log,
        )

        self.result["clang"] = merged["files_parsed"]
        hard = [d for d in merged["diagnostics"] if d.get("severity", 0) >= 3]
        soft = [d for d in merged["diagnostics"] if d.get("severity", 0) == 2]
        self.result["diagnostics"] = (hard + soft)[:60]
        self.result["diagnostics_summary"] = {
            "total": len(merged["diagnostics"]),
            "errors": len(hard),
            "warnings": len(soft),
        }
        self.log(f"  diagnostics: {len(hard)} errors, {len(soft)} warnings")

        quality = {entry["file"]: entry.get("quality", "full") for entry in merged["files_parsed"]}
        degraded = sorted(f for f, q in quality.items() if q != "full")
        self.result["parse_quality"] = {
            "files": quality,
            "degraded": degraded[:40],
            "degraded_count": len(degraded),
        }
        if degraded:
            self.log(f"  parse quality: {len(degraded)} file(s) only partially understood")

        preprocessor = Preprocessor(config, toolchain, log=self.log)
        pp_info: dict = {"available": False}
        if config.preprocess and preprocessor.available:
            started = time.time()
            pp_info = preprocessor.run(sources, os.path.join(out_dir, "preprocessed"))
            pp_info["seconds"] = round(time.time() - started, 1)
            self.log(f"  clang -E pass: {pp_info.get('files', 0)} units "
                     f"in {pp_info['seconds']}s")
        self.result["preprocess"] = pp_info
        pp_index = _pp_index(preprocessor)

        def coverage(rel_file: str, line: int) -> bool:
            absolute = os.path.join(config.source_root, rel_file.replace("/", os.sep))
            unit = pp_index.get(os.path.normcase(os.path.normpath(absolute)))
            return True if unit is None else unit.covers(absolute, line)

        ts = TreeSitterExtractor(config, log=self.log)
        if "treesitter" in engine and ts.available:
            started = time.time()
            ts.analyze(sources + headers)
            merge = ts.merge_into_graph(graph, coverage=coverage)
            self.result["tree_sitter"] = ts.summary() | merge
            self.log(f"  tree-sitter pass: {time.time() - started:.1f}s "
                     f"({ts.summary()['entities']} nodes checked, "
                     f"{merge['dead_code_nodes']} only present in text)")
        else:
            self.result["tree_sitter"] = {"available": False}

        self._attach_expanded_code(graph, expansions, preprocessor, pp_index)
        if degraded:
            degraded_set = set(degraded)
            for node in graph.nodes.values():
                if node.get("file") in degraded_set and not node.get("external"):
                    node["low_confidence"] = True
        _attach_snippets(graph)
        self.result["expansions"] = len(expansions)

        if detect_only:
            return self.result

        stats = graph.dump(out_dir)
        graph.to_graphml(os.path.join(out_dir, "graph.graphml"))
        _dump_expansions(expansions, out_dir)
        self.result["graph"] = stats
        self._add_insights(graph, expansions)

        write_visualisation(graph, config, os.path.join(out_dir, "ckg.html"), self.result)
        write_report(graph, config, self.result, _ExpansionView(expansions),
                     os.path.join(out_dir, "报告.md"))
        toolchain.save_probes(out_dir)
        with open(os.path.join(out_dir, "run_meta.json"), "w", encoding="utf-8") as fh:
            json.dump(self.result, fh, ensure_ascii=False, indent=2, default=str)
        return self.result

    # ------------------------------------------------------------------
    def _attach_expanded_code(self, graph, expansions, preprocessor, pp_index) -> None:
        if not preprocessor.files:
            return
        for node in graph.nodes.values():
            path, line = node.get("path"), node.get("line")
            if not path or not line:
                continue
            unit = pp_index.get(os.path.normcase(os.path.normpath(path)))
            if unit is None:
                continue
            text = unit.lookup(path, int(line))
            if text and text != _snippet_first_line(node):
                node["expanded_code"] = text[:400]
        # the authoritative expansion text for every macro use site
        for expansion in expansions:
            absolute = os.path.join(
                self.config.source_root, expansion.file.replace("/", os.sep)
            )
            unit = pp_index.get(os.path.normcase(os.path.normpath(absolute)))
            if unit is None:
                continue
            expansion.expanded_compiler = unit.lookup(absolute, expansion.line)[:300]

    def _add_insights(self, graph, expansions) -> None:
        call_out: dict[str, int] = {}
        call_in: dict[str, int] = {}
        macro_use: dict[str, int] = {}
        variable_use: dict[str, int] = {}
        for edge in graph.edges.values():
            kind = edge["type"]
            if kind == "CALLS":
                call_out[edge["head"]] = call_out.get(edge["head"], 0) + 1
                call_in[edge["tail"]] = call_in.get(edge["tail"], 0) + 1
            elif kind == "USES_MACRO":
                macro_use[edge["tail"]] = macro_use.get(edge["tail"], 0) + 1
            elif kind in ("READS", "WRITES"):
                variable_use[edge["tail"]] = variable_use.get(edge["tail"], 0) + 1

        def top(mapping: dict[str, int], count: int = 15) -> list[dict]:
            rows = []
            for node_id, value in sorted(mapping.items(), key=lambda kv: -kv[1])[:count]:
                node = graph.nodes.get(node_id, {})
                rows.append({"name": node.get("name", node_id), "type": node.get("type"),
                             "file": node.get("file"), "value": value})
            return rows

        called = set(call_in)
        unreferenced = []
        for node in graph.nodes.values():
            if node.get("type") != "FUNCTION" or not node.get("is_definition"):
                continue
            if node["id"] in called or node.get("dead_code"):
                continue
            name = node.get("name", "")
            if name.lower().startswith("main") or name.lower().endswith(("_cb", "_callback")):
                continue
            unreferenced.append({"name": name, "file": node.get("file"), "line": node.get("line")})

        dependencies = set()
        for edge in graph.edges.values():
            if edge["type"] != "INCLUDES":
                continue
            head = graph.nodes.get(edge["head"], {})
            tail = graph.nodes.get(edge["tail"], {})
            if head.get("file") and tail.get("file") and head["file"] != tail["file"]:
                dependencies.add(f"{head['file']} -> {tail['file']}")

        self.result["insights"] = {
            "call_hubs": top(call_out),
            "most_called": top(call_in),
            "most_used_macros": top(macro_use),
            "hottest_variables": top(variable_use, 20),
            "unreferenced_functions": sorted(unreferenced, key=lambda r: r["name"])[:40],
            "unreferenced_count": len(unreferenced),
            "file_dependencies": sorted(dependencies),
            "macro_defined_but_unused": sorted(
                node["name"] for node in graph.nodes.values()
                if node.get("type") == "MACRO" and not node.get("external")
                and not macro_use.get(node["id"])
            )[:60],
            "expanded_entities": sum(1 for n in graph.nodes.values() if n.get("expanded_code")),
            "expansions_with_compiler_text": sum(
                1 for e in expansions if getattr(e, "expanded_compiler", "")
            ),
            "dead_code": [
                {"name": n.get("name"), "type": n.get("type"),
                 "file": n.get("file"), "line": n.get("line")}
                for n in graph.nodes.values() if n.get("dead_code")
            ][:60],
            "auto_generated": sorted(
                n["name"] for n in graph.nodes.values() if n.get("auto_generated")
            )[:80],
        }


# ----------------------------------------------------------------------
class _ExpansionView:
    """Adapter so the report writer can stay independent of the pipeline."""

    def __init__(self, expansions) -> None:
        self.expansions = expansions


def _dump_expansions(expansions, out_dir: str) -> None:
    with open(os.path.join(out_dir, "macro_expansions.json"), "w", encoding="utf-8") as fh:
        json.dump([e.as_reference_record() for e in expansions], fh,
                  ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "macro_expansions_detailed.json"), "w", encoding="utf-8") as fh:
        json.dump(
            [
                {
                    "file": e.file, "line": e.line, "column": e.column,
                    "invocation": e.invocation, "macro": e.macro,
                    "arguments": e.arguments, "expanded": e.expanded,
                    "expanded_by_compiler": e.expanded_compiler, "owner": e.owner,
                }
                for e in expansions
            ],
            fh, ensure_ascii=False, indent=1,
        )


def _attach_snippets(graph, context: int = 2) -> None:
    cache: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        path, line = node.get("path"), node.get("line")
        if not path or not line or node.get("type") == "FILE":
            continue
        lines = cache.get(path)
        if lines is None:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                lines = []
            cache[path] = lines
        if not lines or line > len(lines):
            continue
        node["snippet"] = "\n".join(
            lines[max(0, line - 1 - context): min(len(lines), line + context)]
        )[:600]


def _snippet_first_line(node: dict) -> str:
    snippet = node.get("snippet") or ""
    return snippet.split("\n")[0].strip() if snippet else ""


def _pp_index(preprocessor: Preprocessor) -> dict:
    index: dict[str, object] = {}
    for unit in preprocessor.files.values():
        index.setdefault(os.path.normcase(os.path.normpath(unit.source)), unit)
        for included in unit._markers:
            index.setdefault(included, unit)
    return index


def _clang_major(clang) -> int:
    try:
        import re

        match = re.search(r"clang version (\d+)", clang.version)
        return int(match.group(1)) if match else 18
    except Exception:
        return 18


def build_from_path(
    path: str,
    output_dir: str | None = None,
    jobs: int = 0,
    engine: str = "clang+treesitter",
    log=print,
    **overrides,
) -> dict:
    """Zero-configuration entry point: detect the project, then build."""
    detection = autoconfig.detect(path)
    display = lambda message="": log(message)  # noqa: E731
    display(f"  detected build provider: {detection.provider}")
    for item in detection.evidence:
        display(f"    - {item}")
    if detection.warnings:
        for warning in detection.warnings:
            display(f"    ! {warning}")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "ckg-out")
    data = detection.to_config_dict(output_dir)
    data.update(overrides)
    config = ProjectConfig(
        source_root=data["source_root"],
        output_dir=os.path.abspath(data["output_dir"]),
        project_name=data["project_name"],
        title=data.get("title", ""),
        include_dirs=[d if os.path.isabs(d) else os.path.join(data["source_root"], d)
                      for d in data["include_dirs"]],
        std_shim_dirs=[_resolve_from(data["source_root"], d) for d in data.get("std_shim_dirs", [])],
        shim_include_dirs=[_resolve_from(data["source_root"], d) for d in data["shim_include_dirs"]],
        excludes=data["excludes"],
        defines=data["defines"],
        std=data["std"],
        target=data["target"],
        compile_commands=data.get("compile_commands", ""),
        auto_repair=data.get("auto_repair", True),
        tree_sitter=data.get("tree_sitter", True),
        preprocess=data.get("preprocess", True),
        clang_driver=data.get("clang_driver", ""),
        jobs=jobs,
    )
    pipeline = Pipeline(config, log=log)
    pipeline.result["build_provider"] = detection.provider
    pipeline.result["detection"] = detection.as_dict()
    return pipeline.run(jobs=jobs, engine=engine)


def _resolve_from(root: str, path: str) -> str:
    if os.path.isabs(path):
        return path
    tool_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(root, path), os.path.join(tool_root, path)]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[-1]
