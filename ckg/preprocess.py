"""Genuine macro expansion via a real clang driver (``clang -E``).

``clang -E`` writes ``# <line> "<file>"`` markers between the expanded chunks.
Those markers let us map any ``(file, line)`` of the original sources onto the
line of the fully preprocessed output that it turned into, which is how the
knowledge graph can show *the code after the preprocessor ran*.

Any clang-family driver works; the toolchain picks, in order:

1. ``clang_driver`` from the run configuration,
2. ``clang`` / ``clang.exe`` from ``PATH``,
3. the clang that ships inside the ``ziglang`` wheel (``python -m ziglang cc``).
"""

from __future__ import annotations

import bisect
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .languages import classify

MARKER_RE = re.compile(r'^#\s+(\d+)\s+"([^"]*)"(.*)$')


class PreprocessedFile:
    """The ``.i`` output of one translation unit, indexed by source position."""

    def __init__(self, source: str, output_path: str, lines: list[str]) -> None:
        self.source = source
        self.output_path = output_path
        self.lines = lines
        self._markers: dict[str, list[tuple[int, int]]] = {}
        self._ordered_markers: list[tuple[int, str, int]] = []
        self._coverage_cache: dict[str, list[tuple[int, int]]] = {}
        self._build_index()

    def _build_index(self) -> None:
        pending: dict[str, list[tuple[int, int]]] = {}
        for index, text in enumerate(self.lines):
            match = MARKER_RE.match(text.strip())
            if not match:
                continue
            src_line = int(match.group(1))
            src_file = os.path.normcase(os.path.normpath(match.group(2)))
            pending.setdefault(src_file, []).append((src_line, index + 1))
            self._ordered_markers.append((index, src_file, src_line))
        for key, pairs in pending.items():
            pairs.sort()
            self._markers[key] = pairs

    def lookup(self, file: str, line: int) -> str:
        """Preprocessed text for ``file:line`` (empty string when unknown)."""
        key = os.path.normcase(os.path.normpath(file))
        pairs = self._markers.get(key)
        if not pairs:
            return ""
        starts = [p[0] for p in pairs]
        idx = bisect.bisect_right(starts, line) - 1
        if idx < 0:
            return ""
        src_start, out_start = pairs[idx]
        out_line = out_start + (line - src_start)
        if 0 <= out_line < len(self.lines):
            return self.lines[out_line].strip()
        return ""

    def block(self, file: str, start_line: int, end_line: int, limit: int = 40) -> str:
        out = []
        for line in range(start_line, min(end_line, start_line + limit) + 1):
            text = self.lookup(file, line)
            if text:
                out.append(text)
        return "\n".join(out)

    def covered_ranges(self, file: str) -> list[tuple[int, int]]:
        """Source line ranges of ``file`` that survived preprocessing.

        Lines inside ``#if 0`` blocks (or otherwise inactive branches) produce no
        marker at all, which is how the toolchain detects dead code.
        """
        key = os.path.normcase(os.path.normpath(file))
        if key not in self._coverage_cache:
            ranges: dict[str, list[tuple[int, int]]] = {}
            markers = self._ordered_markers
            for index, (out_index, marker_file, src_line) in enumerate(markers):
                next_out = markers[index + 1][0] if index + 1 < len(markers) else len(self.lines)
                span = max(0, next_out - out_index - 1)
                if span <= 0:
                    continue
                ranges.setdefault(marker_file, []).append((src_line, src_line + span - 1))
            self._coverage_cache = ranges
        return self._coverage_cache.get(key, [])

    def covers(self, file: str, line: int) -> bool:
        for start, end in self.covered_ranges(file):
            if start <= line <= end:
                return True
        return False


class Preprocessor:
    def __init__(self, config, toolchain=None, log=print) -> None:
        self.config = config
        self.toolchain = toolchain
        self.log = log
        self.command = (toolchain.driver if toolchain and toolchain.available
                        else self._detect_driver())
        self.files: dict[str, PreprocessedFile] = {}
        self.failures: list[dict] = []

    def _detect_driver(self) -> list[str] | None:
        if self.config.clang_driver:
            return [self.config.clang_driver]
        for name in ("clang", "clang.exe"):
            found = shutil.which(name)
            if found:
                return [found]
        try:
            import ziglang  # noqa: F401

            return [sys.executable, "-m", "ziglang", "cc"]
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return bool(self.command)

    def describe(self) -> str:
        return " ".join(self.command) if self.command else "(no clang driver found)"

    def _args(self, source: str) -> list[str]:
        args: list[str] = []
        language = classify(source)
        target = ""
        hosted = False
        if self.toolchain is not None and self.toolchain.available and language:
            target = self.toolchain.effective_target(language, self.config.target)
            hosted = self.toolchain.is_hosted(language, target)
        if not hosted:
            for directory in self.config.std_shim_dirs:
                args += ["-I", directory]
        for directory in self.config.shim_include_dirs:
            args += ["-I", directory]
        for directory in self.config.include_dirs:
            args += ["-I", directory]
        args += ["-std=" + self.config.std]
        for define in self.config.defines:
            args.append("-D" + define)
        args += list(self.config.extra_args)
        if self.toolchain is not None and self.toolchain.available:
            probe = self.toolchain.probe(language, target)
            for define in probe.defines:
                args.append("-D" + define)
            for path in probe.includes:
                args += ["-isystem", path]
            if self.toolchain.compat_header:
                args += ["-include", self.toolchain.compat_header]
        if target:
            args += ["-target", self._driver_target(target)]
        if language:
            args += ["-x", language.clang_kind]
        args += ["-E", source]
        return args

    def _driver_target(self, target: str) -> str:
        """Zig parses the target triple itself and spells bare-metal as freestanding."""
        if self.command and "ziglang" in " ".join(self.command):
            return target.replace("-none-", "-freestanding-")
        return target

    def run(self, sources: list[str], out_dir: str) -> dict:
        if not self.available:
            self.log("  no clang driver available - skipping real preprocessing")
            return {"available": False, "files": 0}
        os.makedirs(out_dir, exist_ok=True)
        cache = os.path.join(tempfile.gettempdir(), "ckg-zig-cache")
        env = dict(os.environ)
        env.setdefault("ZIG_GLOBAL_CACHE_DIR", os.path.join(cache, "global"))
        env.setdefault("ZIG_LOCAL_CACHE_DIR", os.path.join(cache, "local"))
        for source in sources:
            name = os.path.splitext(os.path.basename(source))[0] + ".i"
            target = os.path.join(out_dir, name)
            command = list(self.command)
            if self._is_zig(command) and classify(source) and classify(source).is_cpp:
                command = command[:-1] + ["c++"] if command[-1] == "cc" else command + ["c++"]
            try:
                proc = subprocess.run(
                    command + self._args(source),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.failures.append({"file": source, "error": str(exc)})
                continue
            if proc.returncode != 0 and not proc.stdout:
                self.failures.append({"file": source, "error": (proc.stderr or "").strip()[:400]})
                continue
            lines = proc.stdout.splitlines()
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(proc.stdout)
            self.files[os.path.normcase(os.path.normpath(source))] = PreprocessedFile(
                source, target, lines
            )
        return {
            "available": True,
            "driver": self.describe(),
            "files": len(self.files),
            "failures": len(self.failures),
            "out_dir": out_dir,
        }

    @staticmethod
    def _is_zig(command: list[str]) -> bool:
        return "ziglang" in " ".join(command)

    def for_file(self, path: str) -> PreprocessedFile | None:
        return self.files.get(os.path.normcase(os.path.normpath(path)))
