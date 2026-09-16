"""Run the clang extractor over many translation units in parallel.

libclang is a C library with per-translation-unit state, so the safest way to
use several cores is one process per unit: each worker builds a small
``KnowledgeGraph`` and ships plain dictionaries back, which the parent merges.
Sets of macro definitions and expansion sites are unioned as well so that the
macro post-processing can run exactly once at the end.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from dataclasses import asdict

from .graph_builder import KnowledgeGraph

_GLOBAL: dict = {}


def _worker_init(payload: dict) -> None:
    _GLOBAL.update(payload)
    clang = _GLOBAL["clang"]
    _GLOBAL["extractor_proto"] = None
    _GLOBAL["clang"] = clang


def _worker(task: tuple[str, list[str]]) -> dict:
    """Extract a single translation unit and return plain data."""
    from .extract_clang import ClangExtractor

    source, extra_args = task
    payload = _GLOBAL
    config = payload["config"]
    clang = payload["clang"]
    args_builder = payload["args_builder"]
    graph = KnowledgeGraph()
    extractor = ClangExtractor(graph, config, log=lambda *_: None, args_builder=args_builder)
    extractor.run(clang, [source], extra_args, finalize=False)
    return {
        "source": source,
        "nodes": list(graph.nodes.values()),
        "edges": list(graph.edges.values()),
        "diagnostics": extractor.diagnostics,
        "files_parsed": extractor.files_parsed,
        "expansions": [_expansion_dict(e) for e in extractor.expansions],
        "macro_defs": {name: _macro_dict(d) for name, d in extractor.macro_defs.items()},
        "macro_locations": {
            name: [_macro_dict(d) for d in defs]
            for name, defs in extractor.macro_def_locations.items()
        },
        "pointer_assignments": {k: sorted(v) for k, v in extractor.pointer_assignments.items()},
        "touched_files": sorted(extractor.touched_files),
    }


def _macro_dict(definition) -> dict:
    data = asdict(definition)
    data["tokens"] = [list(t) for t in definition.tokens]
    return data


def _macro_from_dict(data: dict):
    from .macro_engine import MacroDef

    tokens = [(t[0], bool(t[1])) for t in data.get("tokens", [])]
    return MacroDef(
        name=data["name"], file=data["file"], line=data["line"], column=data["column"],
        end_line=data["end_line"], end_column=data["end_column"],
        is_function_like=data["is_function_like"], params=list(data["params"]),
        tokens=tokens, raw_text=data.get("raw_text", ""), doc=data.get("doc", ""),
    )


def _expansion_dict(expansion) -> dict:
    return {
        "file": expansion.file, "line": expansion.line, "column": expansion.column,
        "end_line": expansion.end_line, "end_column": expansion.end_column,
        "invocation": expansion.invocation, "macro": expansion.macro,
        "expanded": expansion.expanded, "arguments": list(expansion.arguments),
        "owner": expansion.owner,
    }


def _expansion_from_dict(data: dict):
    from .macro_engine import Expansion

    return Expansion(**data)


def extract_parallel(
    config,
    sources: list[str],
    args_builder,
    jobs: int = 0,
    log=print,
) -> dict:
    """Parse ``sources`` across processes; returns merged, serialisable data."""
    from .clang_capi import Clang

    if jobs <= 0:
        jobs = max(1, min(os.cpu_count() or 1, 8))
    tasks = [(source, []) for source in sources]
    if jobs == 1 or len(tasks) <= 1:
        _worker_init({"config": config, "clang": Clang.instance(), "args_builder": args_builder})
        results = [_worker(task) for task in tasks]
    else:
        # a thread pool is enough: libclang releases the GIL and each call is
        # independent, which also avoids re-importing the package per worker
        from concurrent.futures import ThreadPoolExecutor

        _worker_init({"config": config, "clang": Clang.instance(), "args_builder": args_builder})
        results = []
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for index, result in enumerate(pool.map(_worker, tasks), 1):
                results.append(result)
                if log and index % 25 == 0:
                    log(f"    parsed {index}/{len(tasks)} translation units")
    return _merge_results(results)


def _merge_results(results: list[dict]) -> dict:
    graph = KnowledgeGraph()
    diagnostics: list[dict] = []
    files_parsed: list[dict] = []
    macro_defs: dict = {}
    macro_locations: dict = {}
    expansions: dict = {}
    pointer_assignments: dict = {}
    touched: set[str] = set()

    for result in results:
        graph.merge(result["nodes"], result["edges"])
        diagnostics.extend(result["diagnostics"])
        files_parsed.extend(result["files_parsed"])
        for name, data in result["macro_defs"].items():
            macro_defs.setdefault(name, data)
        for name, defs in result["macro_locations"].items():
            macro_locations.setdefault(name, []).extend(defs)
        for data in result["expansions"]:
            key = (data["file"], data["line"], data["column"], data["macro"])
            expansions.setdefault(key, data)
        for var, targets in result["pointer_assignments"].items():
            pointer_assignments.setdefault(var, set()).update(targets)
        touched.update(result["touched_files"])
    return {
        "graph": graph,
        "diagnostics": diagnostics,
        "files_parsed": files_parsed,
        "macro_defs": {k: _macro_from_dict(v) for k, v in macro_defs.items()},
        "macro_locations": {
            k: [_macro_from_dict(d) for d in v] for k, v in macro_locations.items()
        },
        "expansions": [
            _expansion_from_dict(d)
            for d in sorted(expansions.values(), key=lambda d: (d["file"], d["line"], d["column"]))
        ],
        "pointer_assignments": pointer_assignments,
        "touched_files": touched,
    }
