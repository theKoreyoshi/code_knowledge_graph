"""Project configuration for a knowledge-graph run."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class ProjectConfig:
    source_root: str
    output_dir: str
    project_name: str = "project"
    title: str = ""
    include_dirs: list[str] = field(default_factory=list)
    std_shim_dirs: list[str] = field(default_factory=list)
    shim_include_dirs: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    std: str = "gnu11"
    target: str = ""
    extra_args: list[str] = field(default_factory=list)
    compile_commands: str = ""
    auto_repair: bool = True
    extensions: list[str] = field(default_factory=list)
    include_globs: list[str] = field(default_factory=list)
    tree_sitter: bool = True
    preprocess: bool = True
    clang_driver: str = ""
    jobs: int = 0

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "ProjectConfig":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        base = os.path.dirname(os.path.abspath(path))
        root = _resolve(base, raw["source_root"])
        out = _resolve(base, raw.get("output_dir", "out"))
        shims = [_resolve(base, d) for d in raw.get("shim_include_dirs", [])]
        std_shims = [_resolve(base, d) for d in raw.get("std_shim_dirs", [])]
        config = cls(
            source_root=root,
            output_dir=out,
            project_name=raw.get("project_name", os.path.basename(root)),
            include_dirs=[_resolve(root, d) for d in raw.get("include_dirs", [])],
            std_shim_dirs=std_shims,
            shim_include_dirs=shims,
            excludes=raw.get("excludes", []),
            defines=raw.get("defines", []),
            std=raw.get("std", "c99"),
            target=raw.get("target", ""),
            extra_args=raw.get("extra_args", []),
            compile_commands=(
                _resolve(base, raw["compile_commands"]) if raw.get("compile_commands") else ""
            ),
            auto_repair=raw.get("auto_repair", True),
            extensions=raw.get("extensions", []),
            include_globs=raw.get("include_globs", []),
            tree_sitter=raw.get("tree_sitter", True),
            preprocess=raw.get("preprocess", True),
            clang_driver=raw.get("clang_driver", ""),
            title=raw.get("title", ""),
            jobs=raw.get("jobs", 0),
        )
        return config

    def discover_sources(self) -> tuple[list[str], list[str]]:
        from .languages import DEFAULT_EXTENSIONS, classify

        extensions = tuple(self.extensions) or DEFAULT_EXTENSIONS
        output_root = os.path.normcase(os.path.normpath(self.output_dir))
        c_files: list[str] = []
        headers: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.source_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in {".git", "__pycache__"}
                and not _excluded(os.path.join(dirpath, d), self.excludes, self.source_root)
                and not os.path.normcase(os.path.normpath(os.path.join(dirpath, d))).startswith(output_root)
            ]
            for name in filenames:
                full = os.path.join(dirpath, name)
                # never analyse our own generated output (stubs, preprocessed .i)
                if os.path.normcase(os.path.normpath(full)).startswith(output_root):
                    continue
                if _excluded(full, self.excludes, self.source_root):
                    continue
                lower = name.lower()
                language = classify(name)
                if language is None or not lower.endswith(extensions):
                    continue
                if self.include_globs and not _matches_any(full, self.include_globs, self.source_root):
                    continue
                if language.is_header:
                    headers.append(full)
                else:
                    c_files.append(full)
        return sorted(c_files), sorted(headers)

    def resolved(self) -> "ProjectConfig":
        """Fill in defaults that depend on the detected project shape."""
        if not self.target:
            self.target = ""
        return self


def _matches_any(path: str, patterns: list[str], root: str) -> bool:
    import fnmatch

    rel = os.path.relpath(path, root).replace("\\", "/")
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(path), p) for p in patterns)


def _resolve(base: str, path: str) -> str:
    if not path:
        return base
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


def _excluded(path: str, patterns: list[str], root: str) -> bool:
    if not patterns:
        return False
    rel = os.path.relpath(path, root).replace("\\", "/")
    name = os.path.basename(path)
    for pattern in patterns:
        if pattern in rel or pattern == name:
            return True
    return False
