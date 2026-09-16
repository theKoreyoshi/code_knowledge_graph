"""Backwards-compatible entry point.

``run_ckg.py --config graph_config.json`` still works; the generic CLI is
``python -m ckg build <path>``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ckg.config import ProjectConfig  # noqa: E402
from ckg.pipeline import Pipeline, build_from_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a C/C++ code knowledge graph")
    parser.add_argument("--config", help="configuration file (see examples/foc/graph_config.json)")
    parser.add_argument("path", nargs="?", help="project root; auto-detects when --config is absent")
    parser.add_argument("-o", "--output", help="output directory")
    parser.add_argument("-j", "--jobs", type=int, default=0, help="parallel workers")
    parser.add_argument("--engine", default="clang+treesitter",
                        choices=["clang", "clang+treesitter", "treesitter"])
    parser.add_argument("--no-preprocess", action="store_true")
    parser.add_argument("--no-treesitter", action="store_true")
    parser.add_argument("--no-repair", action="store_true")
    args = parser.parse_args(argv)

    started = time.time()
    if args.config:
        config = ProjectConfig.load(args.config)
        if args.output:
            config.output_dir = os.path.abspath(args.output)
        print(f"[code-kg] {config.project_name}  ({config.source_root})")
        result = Pipeline(config, log=print).run(jobs=args.jobs, engine=args.engine)
        out_dir = config.output_dir
    else:
        path = os.path.abspath(args.path or ".")
        print(f"[code-kg] auto-detect + build: {path}")
        result = build_from_path(
            path, output_dir=args.output, jobs=args.jobs, engine=args.engine, log=print,
            preprocess=not args.no_preprocess,
            tree_sitter=not args.no_treesitter,
            auto_repair=not args.no_repair,
        )
        out_dir = args.output or os.path.join(path, "ckg-out")

    stats = result.get("graph") or {}
    print("")
    print(f"  entities : {stats.get('entities')}")
    print(f"  relations: {stats.get('relations')}")
    print(f"  elapsed  : {time.time() - started:.1f}s")
    print(f"  output   : {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
