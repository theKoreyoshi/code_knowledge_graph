"""Command line interface: ``ckg init`` / ``build`` / ``viz`` / ``query`` / ``info``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, autoconfig
from .config import ProjectConfig
from .pipeline import Pipeline, build_from_path


def log(message: str = "") -> None:
    print(message, flush=True)


# ----------------------------------------------------------------------
def cmd_init(args) -> int:
    root = os.path.abspath(args.path or ".")
    detection = autoconfig.detect(root)
    data = detection.to_config_dict(args.output_dir or "./ckg-out")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        log(f"wrote {args.out}")
    log("detected:")
    log(f"  provider      : {detection.provider}")
    log(f"  languages     : {', '.join(detection.languages) or 'none'}")
    log(f"  include dirs  : {len(data['include_dirs'])}")
    for directory in data["include_dirs"][:12]:
        log(f"      {directory}")
    if len(data["include_dirs"]) > 12:
        log(f"      ... {len(data['include_dirs']) - 12} more")
    log(f"  defines       : {len(data['defines'])} {data['defines'][:8]}")
    log(f"  std           : {data['std']}")
    log(f"  target        : {data['target'] or '(host default)'}")
    log(f"  embedded      : {detection.embedded}")
    for item in detection.evidence:
        log(f"  evidence      : {item}")
    if not args.out:
        log()
        log(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
    return 0


def cmd_build(args) -> int:
    if args.config:
        config = ProjectConfig.load(args.config)
        if args.out:
            config.output_dir = os.path.abspath(args.out)
        log(f"[ckg] {config.project_name}  ({config.source_root})")
        started = time.time()
        result = Pipeline(config, log=log).run(
            jobs=args.jobs or 0,
            engine=args.engine,
            detect_only=False,
        )
    else:
        path = os.path.abspath(args.path or ".")
        log(f"[ckg] auto-detect + build: {path}")
        started = time.time()
        result = build_from_path(
            path,
            output_dir=args.out,
            jobs=args.jobs or 0,
            engine=args.engine,
            log=log,
            tree_sitter=not args.no_treesitter,
            preprocess=not args.no_preprocess,
            auto_repair=not args.no_repair,
        )
    stats = result.get("graph") or {}
    log("")
    log(f"  entities : {stats.get('entities')}")
    log(f"  relations: {stats.get('relations')}")
    log(f"  elapsed  : {time.time() - started:.1f}s")
    out_dir = result.get("output_dir") or _out_dir_from(result)
    if out_dir:
        log(f"  output   : {out_dir}")
    return 0


def _out_dir_from(result: dict) -> str:
    return ""


def cmd_viz(args) -> int:
    from .graph_builder import KnowledgeGraph
    from .visualize import write_visualisation

    config = ProjectConfig.load(args.config) if args.config else None
    out_dir = args.dir or (config.output_dir if config else os.getcwd())
    entity_path = os.path.join(out_dir, "entity.json")
    relation_path = os.path.join(out_dir, "relation.json")
    for path in (entity_path, relation_path):
        if not os.path.isfile(path):
            log(f"missing {path}; run `ckg build` first")
            return 1
    graph = KnowledgeGraph()
    with open(entity_path, encoding="utf-8") as fh:
        for record in json.load(fh):
            graph.node(record["id"], record["type"], record.get("name", ""),
                       **{k: v for k, v in record.items() if k not in ("id", "type", "name")})
    with open(relation_path, encoding="utf-8") as fh:
        for record in json.load(fh):
            graph.edge(record["head"], record["tail"], record["type"],
                       **{k: v for k, v in record.items()
                          if k not in ("head", "tail", "type", "count")})
    if config is None:
        from .config import ProjectConfig as PC

        config = PC(source_root=out_dir, output_dir=out_dir,
                    project_name=os.path.basename(os.path.abspath(out_dir)))
    target = os.path.join(out_dir, "ckg.html")
    write_visualisation(graph, config, target, {})
    log(f"wrote {target}")
    return 0


def cmd_query(args) -> int:
    out_dir = args.dir
    with open(os.path.join(out_dir, "entity.json"), encoding="utf-8") as fh:
        nodes = json.load(fh)
    with open(os.path.join(out_dir, "relation.json"), encoding="utf-8") as fh:
        relations = json.load(fh)
    by_id = {n["id"]: n for n in nodes}
    needle = args.symbol.lower()
    matches = [n for n in nodes if n.get("name", "").lower() == needle]
    if not matches:
        matches = [n for n in nodes if needle in n.get("name", "").lower()]
    matches = matches[: args.limit]
    if not matches:
        log(f"no entity matching {args.symbol!r}")
        return 1
    for node in matches:
        log("=" * 72)
        log(f"{node['type']}  {node['name']}   [{node.get('file')}:{node.get('line')}]")
        for key in ("signature", "return_type", "type_spelling", "storage_class", "scope"):
            if node.get(key):
                log(f"  {key}: {node[key]}")
        if node.get("metrics"):
            log(f"  metrics: {node['metrics']}")
        if node.get("expanded_code"):
            log(f"  expanded: {node['expanded_code'][:160]}")
        outgoing = [r for r in relations if r["head"] == node["id"]]
        incoming = [r for r in relations if r["tail"] == node["id"]]
        for direction, items in (("out", outgoing), ("in", incoming)):
            for rel in items[: args.relations]:
                other = by_id.get(rel["tail"] if direction == "out" else rel["head"], {})
                arrow = "->" if direction == "out" else "<-"
                extra = f" (via macro {rel['via_macro']})" if rel.get("via_macro") else ""
                log(f"  {arrow} {rel['type']:<14} {other.get('type',''):<10} {other.get('name','')}{extra}")
            if len(items) > args.relations:
                log(f"  {direction}: ... {len(items) - args.relations} more")
    return 0


def cmd_info(args) -> int:
    from .clang_capi import Clang
    from .languages import C, CPP
    from .toolchain import Toolchain

    clang = Clang.instance()
    log(f"code-kg {__version__}")
    log(f"libclang      : {clang.version.splitlines()[0]}")
    log(f"library       : {clang.path}")
    toolchain = Toolchain(None, log=log)
    log(f"driver        : {toolchain.describe()}")
    for language in (C, CPP):
        probe = toolchain.probe(language, "")
        log(f"  {language.name:<6}: {len(probe.includes)} include dirs, "
            f"{len(probe.defines)} predefined macros, triple={probe.triple or 'default'}")
        for path in probe.includes[:6]:
            log(f"      {path}")
    try:
        import tree_sitter

        log(f"tree-sitter   : {tree_sitter.__version__}")
    except Exception:
        log("tree-sitter   : not installed")
    try:
        import ziglang  # noqa: F401

        log("ziglang       : installed (provides a clang driver + C/C++ headers)")
    except Exception:
        log("ziglang       : not installed")
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ckg",
        description="Build a code knowledge graph for C/C++ projects",
    )
    parser.add_argument("--version", action="version", version=f"code-kg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="detect a project and print/write a configuration")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("-o", "--out", help="write the configuration to this file")
    init.add_argument("--output-dir", default="./ckg-out")
    init.set_defaults(func=cmd_init)

    build = sub.add_parser("build", help="build the knowledge graph")
    build.add_argument("path", nargs="?", default=".", help="project root (auto-detects)")
    build.add_argument("-c", "--config", help="use an explicit configuration file")
    build.add_argument("-o", "--out", help="output directory")
    build.add_argument("-j", "--jobs", type=int, default=0, help="parallel workers")
    build.add_argument("--engine", default="clang+treesitter",
                       choices=["clang", "clang+treesitter", "treesitter"])
    build.add_argument("--no-treesitter", action="store_true")
    build.add_argument("--no-preprocess", action="store_true")
    build.add_argument("--no-repair", action="store_true")
    build.set_defaults(func=cmd_build)

    viz = sub.add_parser("viz", help="regenerate the interactive HTML from entity/relation json")
    viz.add_argument("-c", "--config")
    viz.add_argument("-d", "--dir", help="directory holding entity.json/relation.json")
    viz.set_defaults(func=cmd_viz)

    query = sub.add_parser("query", help="inspect a symbol inside a built graph")
    query.add_argument("symbol")
    query.add_argument("-d", "--dir", required=True)
    query.add_argument("--limit", type=int, default=5)
    query.add_argument("--relations", type=int, default=12)
    query.set_defaults(func=cmd_query)

    info = sub.add_parser("info", help="show the detected toolchain")
    info.set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
