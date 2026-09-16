"""Compiler detection and header discovery.

The hard part of parsing an arbitrary C/C++ project is not clang, it is knowing
which include paths, predefined macros and target the build actually uses.  This
module asks a real compiler driver for its own configuration (``-E -v`` prints
the search list, ``-cc1`` prints the exact front-end flags) and replays the
result to libclang.  That makes the extractor independent of the host: any
clang, any GCC, or the clang bundled inside the ``ziglang`` wheel works.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from .languages import Language

PROBE_TIMEOUT = 180


@dataclass
class ToolchainProbe:
    driver: list[str]
    language: str
    target: str
    includes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    triple: str = ""
    ok: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "driver": self.driver,
            "language": self.language,
            "target": self.target,
            "triple": self.triple,
            "includes": self.includes,
            "defines": self.defines,
            "ok": self.ok,
        }


class Toolchain:
    """Locates a compiler driver and caches its configuration per language."""

    def __init__(self, config=None, log=print) -> None:
        self.config = config
        self.log = log
        self.driver = self._detect_driver(config)
        self.cache_dir = ""
        self._probes: dict[tuple, ToolchainProbe] = {}
        self.compat_header = ""

    # ------------------------------------------------------------------
    def _detect_driver(self, config) -> list[str]:
        explicit = getattr(config, "clang_driver", "") if config else ""
        if explicit:
            return explicit.split() if isinstance(explicit, str) else list(explicit)
        for name in ("clang", "clang.exe"):
            found = shutil.which(name)
            if found:
                return [found]
        for name in ("gcc", "gcc.exe"):
            found = shutil.which(name)
            if found:
                return [found]
        try:
            import ziglang  # noqa: F401

            return [sys.executable, "-m", "ziglang", "cc"]
        except Exception:
            return []

    @property
    def available(self) -> bool:
        return bool(self.driver)

    def describe(self) -> str:
        return " ".join(self.driver) if self.driver else "(no compiler driver found)"

    def _is_zig(self) -> bool:
        return "ziglang" in " ".join(self.driver)

    def _driver_for(self, language: Language) -> list[str]:
        """zig uses separate ``cc`` / ``c++`` entry points."""
        if self._is_zig() and language.is_cpp:
            return self.driver[:-1] + ["c++"] if self.driver[-1] == "cc" else self.driver + ["c++"]
        return self.driver

    def _env(self) -> dict:
        env = dict(os.environ)
        if self._is_zig():
            cache = os.path.join(tempfile.gettempdir(), "ckg-zig-cache")
            env.setdefault("ZIG_GLOBAL_CACHE_DIR", os.path.join(cache, "global"))
            env.setdefault("ZIG_LOCAL_CACHE_DIR", os.path.join(cache, "local"))
        return env

    # ------------------------------------------------------------------
    def probe(self, language: Language, target: str = "") -> ToolchainProbe:
        key = (language.name, target)
        cached = self._probes.get(key)
        if cached is not None:
            return cached
        probe = ToolchainProbe(self.driver, language.name, target)
        self._probes[key] = probe
        if not self.available:
            probe.error = "no compiler driver"
            return probe

        workdir = tempfile.mkdtemp(prefix="ckg-probe-")
        suffix = ".cpp" if language.is_cpp else ".c"
        source = os.path.join(workdir, "probe" + suffix)
        with open(source, "w", encoding="utf-8") as fh:
            fh.write("int ckg_probe_symbol;\n")

        args = self._driver_for(language) + ["-E", "-v"]
        if target:
            args += ["-target", self._driver_target(target)]
        args += ["-x", language.clang_kind, source]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=self._env(), cwd=workdir, timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            probe.error = str(exc)
            return probe

        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        probe.includes = _search_paths(text)
        cc1_defines, cc1_includes, triple = _cc1_flags(text)
        if cc1_includes:
            merged = list(probe.includes)
            for path in cc1_includes:
                if path not in merged:
                    merged.append(path)
            probe.includes = merged
        probe.defines = cc1_defines
        probe.triple = triple
        probe.ok = bool(probe.includes)
        if not probe.ok:
            probe.error = (proc.stderr or proc.stdout or "")[-400:]
        return probe

    def _driver_target(self, target: str) -> str:
        """Zig parses the triple itself and calls bare metal 'freestanding'."""
        if self._is_zig():
            return target.replace("-none-", "-freestanding-")
        return target

    def effective_target(self, language: Language, configured: str) -> str:
        """Pick a target that actually has a usable header set.

        On Windows the C++ headers of the bundled toolchain need the MSVC
        runtime, which is not present; a musl target gives a complete libc++.
        """
        if configured:
            return configured
        if sys.platform == "win32" and language.is_cpp and self._is_zig():
            return "x86_64-linux-musl"
        return ""

    def is_hosted(self, language: Language, target: str = "") -> bool:
        """True when the toolchain ships a full C library for this target.

        A hosted target (Linux/Windows/macOS) must use the toolchain's own
        headers; a freestanding target (bare metal) needs the shim headers.
        """
        probe = self.probe(language, target)
        for directory in probe.includes:
            for name in ("stdio.h", "stdlib.h", "string.h"):
                if os.path.isfile(os.path.join(directory, name)):
                    return True
        return False

    # ------------------------------------------------------------------
    def parser_args(
        self,
        language: Language,
        target: str = "",
        extra: list[str] | None = None,
        with_compat: bool = True,
    ) -> list[str]:
        """clang command line that reproduces the toolchain's own environment."""
        probe = self.probe(language, target)
        args = ["-x", language.clang_kind]
        # Pin the target explicitly: libclang's default on Windows is the
        # MSVC-flavoured triple, which predefines size_t and then collides with
        # whatever the project (or a shim) declares.
        resolved_target = target or probe.triple
        if resolved_target:
            args += ["-target", resolved_target]
        args += list(extra or [])
        for define in probe.defines:
            args.append("-D" + define)
        for path in probe.includes:
            args += ["-isystem", path]
        if with_compat and self.compat_header:
            args += ["-include", self.compat_header]
        return args

    # ------------------------------------------------------------------
    def write_compat_header(self, out_dir: str, clang_major: int = 18) -> str:
        """Bridge toolchain headers onto the libclang front-end doing the parsing."""
        directory = os.path.join(out_dir, "_toolchain")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "frontend_compat.h")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_COMPAT_TEMPLATE.replace("__CKG_CLANG_MAJOR__", str(clang_major)))
        self.compat_header = path
        return path

    def save_probes(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        data = {f"{k[0]}|{k[1]}": v.as_dict() for k, v in self._probes.items()}
        with open(os.path.join(directory, "toolchain.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


def _search_paths(text: str) -> list[str]:
    paths: list[str] = []
    inside = False
    for line in text.splitlines():
        if "search starts here" in line:
            inside = True
            continue
        if "End of search list" in line:
            inside = False
            continue
        if not inside:
            continue
        candidate = line.strip()
        if candidate and os.path.isdir(candidate):
            resolved = os.path.abspath(candidate)
            if resolved not in paths:
                paths.append(resolved)
    return paths


_CC1_RE = re.compile(r'"-cc1"|-cc1 ')


def _cc1_flags(text: str) -> tuple[list[str], list[str], str]:
    """Extract (-D defines, -isystem dirs, triple) from the -cc1 line."""
    line = ""
    for candidate in text.splitlines():
        if _CC1_RE.search(candidate) and "-isystem" in candidate:
            line = candidate
            break
    if not line:
        return [], [], ""
    tokens = re.findall(r'"[^"]*"|\S+', line)
    defines: list[str] = []
    includes: list[str] = []
    triple = ""
    index = 0
    while index < len(tokens):
        token = tokens[index].strip('"')
        if token in ("-D", "-U") and index + 1 < len(tokens):
            value = tokens[index + 1].strip('"')
            defines.append((value if token == "-D" else ""))
            index += 2
            continue
        if token.startswith("-D") and len(token) > 2:
            defines.append(token[2:])
        elif token == "-isystem" and index + 1 < len(tokens):
            path = tokens[index + 1].strip('"')
            if os.path.isdir(path):
                includes.append(os.path.abspath(path))
            index += 2
            continue
        elif token == "-triple" and index + 1 < len(tokens):
            triple = tokens[index + 1].strip('"')
            index += 2
            continue
        index += 1
    defines = [d for d in defines if d and not d.startswith("__CKG")]
    return defines, includes, triple


_COMPAT_TEMPLATE = """/* Generated by code-kg. Do not edit.
 *
 * Bridges the header set that ships with the toolchain onto the libclang
 * front-end doing the analysis:
 *   - libc++ is usually newer than the front-end,
 *   - freestanding targets expect the __INT*_C macros that a hosted libc would
 *     normally provide.
 */
#ifndef CKG_CXX_COMPAT_H
#define CKG_CXX_COMPAT_H

#ifndef __INT8_C
#define __INT8_C(c) c
#define __INT16_C(c) c
#define __INT32_C(c) c
#define __INT64_C(c) c##LL
#define __UINT8_C(c) c
#define __UINT16_C(c) c
#define __UINT32_C(c) c##U
#define __UINT64_C(c) c##ULL
#endif

#ifndef _LIBCPP_HARDENING_MODE
#define _LIBCPP_HARDENING_MODE _LIBCPP_HARDENING_MODE_NONE
#endif

#if defined(__clang__) && __clang_major__ < 19
static inline int __ckg_clzg_impl(unsigned long long value, int fallback) {
  return value ? __builtin_clzll(value) : fallback;
}
static inline int __ckg_ctzg_impl(unsigned long long value, int fallback) {
  return value ? __builtin_ctzll(value) : fallback;
}
#define __builtin_clzg(x, ...) __ckg_clzg_impl((unsigned long long)(x), 64)
#define __builtin_ctzg(x, ...) __ckg_ctzg_impl((unsigned long long)(x), 64)
#endif

#endif /* CKG_CXX_COMPAT_H */
"""
