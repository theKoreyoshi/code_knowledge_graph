"""Builds the clang command line for each translation unit.

Precedence, most specific first:

1. the entry from ``compile_commands.json`` when the project provides one,
2. project include dirs / defines detected by :mod:`ckg.autoconfig`,
3. generated stub + prelude paths from :mod:`ckg.shimgen`,
4. the toolchain's own system include paths and predefined macros.
"""

from __future__ import annotations

import os

from .languages import classify

ROBUSTNESS_FLAGS = ["-ferror-limit=0", "-Wno-everything", "-Wno-implicit-function-declaration"]


class ArgBuilder:
    def __init__(self, config, toolchain, repair=None, compile_db=None) -> None:
        self.config = config
        self.toolchain = toolchain
        self.repair = repair
        self.compile_db = compile_db or {}
        self._cache: dict[str, list[str]] = {}

    def for_file(self, path: str) -> list[str]:
        key = os.path.normcase(os.path.normpath(path))
        if key in self._cache:
            return list(self._cache[key])
        args = self._build(path)
        self._cache[key] = args
        return list(args)

    # ------------------------------------------------------------------
    def _build(self, path: str) -> list[str]:
        language = classify(path)
        from_file_db = self.compile_db.get(os.path.normcase(os.path.normpath(path)))
        args: list[str] = []
        if from_file_db:
            args += from_file_db
            if not any(a.startswith("-std=") for a in args) and language:
                args.append("-std=" + self.config.std)
            if language and not any(a == "-x" for a in args):
                args += ["-x", language.clang_kind]
            target = self.toolchain.effective_target(language, self.config.target) if language else ""
            args += self._overlay(language, target)
            if language:
                for define in self.toolchain.probe(language, target).defines:
                    args.append("-D" + define)
                for path in self.toolchain.probe(language, target).includes:
                    args += ["-isystem", path]
        else:
            args += self._synthesized(path, language)
        args += list(self.config.extra_args)
        args += ROBUSTNESS_FLAGS
        return args

    def _synthesized(self, path: str, language) -> list[str]:
        args: list[str] = []
        if language:
            args += ["-x", language.clang_kind]
        args += ["-std=" + self.config.std]
        for define in self.config.defines:
            args.append("-D" + define)
        for directory in self.config.include_dirs:
            args += ["-I", directory]
        if language:
            target = self.toolchain.effective_target(language, self.config.target)
            args += self._overlay(language, target)
            args += self.toolchain.parser_args(language, target)
        else:
            args += self._overlay(None, "")
        return args

    def _overlay(self, language=None, target: str = "") -> list[str]:
        """Stub headers and the auto-generated prelude, in precedence order."""
        args: list[str] = []
        # standard-library shims are only needed on freestanding targets; on a
        # hosted target they would fight with the real libc headers
        if language is None or not self.toolchain.is_hosted(language, target):
            for directory in self.config.std_shim_dirs:
                args += ["-I", directory]
        for directory in self.config.shim_include_dirs:
            args += ["-I", directory]
        if self.repair is not None:
            args += self.repair.parser_args()
        return args
