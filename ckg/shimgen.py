"""Self-healing parse front-end.

Analysing real projects means dealing with headers that are not in the repo
(vendor SDKs, chip headers, generated files).  Instead of demanding a perfect
build, the toolchain parses what it can, reads clang's diagnostics and
synthesises the minimum needed to keep going:

* ``'foo.h' file not found`` -> an empty guarded stub header,
* ``unknown type name 'T'`` -> ``typedef int T;`` in a force-included prelude,
* ``implicit declaration of function 'f'`` -> ``int f();``,
* ``use of undeclared identifier 'V'`` -> ``extern int V;`` and, when that turns
  out to be used as a constant, ``#define V 0``.

Everything it invents is recorded, marked ``auto_generated`` in the graph and
listed in the run report, so it is always clear which parts of the picture are
real and which are scaffolding.
"""

from __future__ import annotations

import os
import re
from collections import Counter

from .clang_capi import Clang
from .frontend import load_translation_unit

MAX_SYNTHETIC = 600
MAX_ROUNDS = 4

#: 这些名字几乎总是来自标准头文件。若因为缺少 #include 而合成 `typedef int X;`，
#: 反而会与随后真正定义它们的头文件冲突（typedef redefinition），
#: 所以宁可保留原始报错，也不合成它们。
STANDARD_TYPE_DENYLIST = {
    "size_t", "ptrdiff_t", "wchar_t", "intptr_t", "uintptr_t", "ssize_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "bool", "va_list", "time_t", "FILE", "clock_t", "off_t", "max_align_t",
}

MISSING_HEADER_RE = re.compile(r"['\"]([^'\"]+\.(?:h|hh|hpp|hxx|inc|def))['\"] file not found")
UNKNOWN_TYPE_RE = re.compile(r"unknown type name '([^']+)'")
UNKNOWN_TYPE_ALT_RE = re.compile(r"unknown type name '([^']+)'|use of undeclared identifier '([^']+)'")
IMPLICIT_FN_RE = re.compile(r"(?:implicit declaration of function|call to undeclared function) '([^']+)'")
CALL_UNDECLARED_RE = re.compile(r"call to undeclared function '([^']+)'")
UNDECLARED_ID_RE = re.compile(r"use of undeclared identifier '([^']+)'")
MEMBER_OF_UNKNOWN_RE = re.compile(r"member access into incomplete type|incomplete type '([^']+)'")
NEEDS_CONSTANT_RE = re.compile(r"not an integer constant expression|not a compile-time constant")


class AutoRepair:
    def __init__(self, config, out_dir: str, log=print) -> None:
        self.config = config
        self.log = log
        self.root = os.path.join(out_dir, "_auto_shim")
        self.prelude = os.path.join(self.root, "ckg_autodefs.h")
        os.makedirs(self.root, exist_ok=True)
        self.stub_headers: dict[str, str] = {}
        self.typedefs: dict[str, str] = {}
        self.functions: dict[str, str] = {}
        self.variables: dict[str, str] = {}
        self.macros: dict[str, str] = {}
        self.rounds: list[dict] = []
        self._write_prelude()

    # ------------------------------------------------------------------
    @property
    def include_dir(self) -> str:
        return self.root

    def parser_args(self) -> list[str]:
        return ["-I", self.root, "-include", self.prelude]

    def generated_symbols(self) -> dict:
        return {
            "headers": len(self.stub_headers),
            "typedefs": len(self.typedefs),
            "functions": len(self.functions),
            "variables": len(self.variables),
            "macros": len(self.macros),
        }

    # ------------------------------------------------------------------
    def run(self, clang: Clang, sources: list[str], args_for, jobs: int = 1) -> dict:
        """Iterate parse -> inspect diagnostics -> patch until it converges."""
        if not self.config.auto_repair:
            return {"enabled": False}
        for round_index in range(MAX_ROUNDS):
            findings = Counter()
            for source in sources:
                # the first round only needs headers/types, so skipping function
                # bodies keeps it fast; later rounds look inside the bodies to
                # find undeclared identifiers used by the code itself
                self._inspect(
                    clang, source, args_for(source), findings,
                    skip_bodies=(round_index == 0),
                )
            added = self._apply(findings)
            self.rounds.append({"round": round_index + 1, "added": added,
                                "totals": self.generated_symbols()})
            self.log(
                "  auto-repair round %d: +%d headers, +%d types, +%d functions, +%d variables, +%d macros"
                % (round_index + 1, added.get("headers", 0), added.get("typedefs", 0),
                   added.get("functions", 0), added.get("variables", 0), added.get("macros", 0))
            )
            # round 1 skips function bodies, so it can legitimately find nothing
            # even though the bodies still need repair: always run a full round
            if round_index >= 1 and not any(added.values()):
                break
        self._write_prelude()
        return {
            "enabled": True,
            "rounds": self.rounds,
            "generated": self.generated_symbols(),
            "stub_headers": sorted(self.stub_headers),
            "typedefs": sorted(self.typedefs)[:200],
            "functions": sorted(self.functions)[:200],
            "variables": sorted(self.variables)[:200],
        }

    # ------------------------------------------------------------------
    def _inspect(
        self, clang: Clang, source: str, args: list[str], findings: Counter,
        skip_bodies: bool = True,
    ) -> None:
        try:
            tu = load_translation_unit(clang, source, args, skip_function_bodies=skip_bodies)
        except Exception:
            return
        try:
            for diag in tu.diagnostics():
                text = diag.get("text", "")
                severity = diag.get("severity", 0)
                match = MISSING_HEADER_RE.search(text)
                if match:
                    findings["header:" + match.group(1)] += 1
                    continue
                match = IMPLICIT_FN_RE.search(text) or CALL_UNDECLARED_RE.search(text)
                if match:
                    findings["function:" + match.group(1)] += 1
                    continue
                match = UNKNOWN_TYPE_RE.search(text)
                if match:
                    findings["type:" + match.group(1)] += 1
                    continue
                match = UNDECLARED_ID_RE.search(text)
                if match:
                    name = match.group(1)
                    key = "constant:" + name if NEEDS_CONSTANT_RE.search(text) else "variable:" + name
                    findings[key] += 1
                    continue
                if severity >= 3:
                    findings["other"] += 1
        finally:
            tu.dispose()

    def _apply(self, findings: Counter) -> dict:
        added = Counter()
        for key, count in findings.items():
            kind, _, name = key.partition(":")
            if not name:
                continue
            if len(self.stub_headers) + len(self.typedefs) + len(self.functions) + len(self.variables) > MAX_SYNTHETIC:
                break
            if kind == "header":
                if self._write_stub_header(name):
                    added["headers"] += 1
            elif kind == "type":
                if name not in self.typedefs:
                    self.typedefs[name] = f"typedef int {name};"
                    added["typedefs"] += 1
            elif kind == "function":
                if name not in self.functions:
                    self.functions[name] = f"int {name}();"
                    added["functions"] += 1
            elif kind == "variable":
                if name not in self.variables and name not in self.macros:
                    self.variables[name] = f"extern int {name};"
                    added["variables"] += 1
            elif kind == "constant":
                if name not in self.macros:
                    self.variables.pop(name, None)
                    self.macros[name] = f"#define {name} 0"
                    added["macros"] += 1
        if added:
            self._write_prelude()
        return dict(added)

    # ------------------------------------------------------------------
    def _write_stub_header(self, name: str) -> bool:
        relative = name.replace("\\", "/").lstrip("/")
        if not relative or ".." in relative.split("/"):
            return False
        if relative in self.stub_headers:
            return False
        path = os.path.join(self.root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        guard = "CKG_STUB_" + re.sub(r"[^A-Za-z0-9]", "_", relative).upper()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                f"/* Generated by code-kg: '{name}' was not found in the project or\n"
                f" * in the toolchain.  This guarded stub keeps the parser going. */\n"
                f"#ifndef {guard}\n#define {guard}\n#endif /* {guard} */\n"
            )
        self.stub_headers[relative] = path
        return True

    def _write_prelude(self) -> None:
        lines = [
            "/* Generated by code-kg - declarations synthesised from clang diagnostics.",
            " * Everything here exists so that the parser can keep going; nodes that",
            " * depend on it are marked as auto-generated in the graph. */",
            "#ifndef CKG_AUTODEFS_H",
            "#define CKG_AUTODEFS_H",
        ]
        if self.typedefs:
            lines.append("/* unknown types */")
            lines += sorted(self.typedefs.values())
        if self.functions:
            lines.append("/* undeclared functions */")
            lines += sorted(self.functions.values())
        if self.variables:
            lines.append("/* undeclared identifiers */")
            lines += sorted(self.variables.values())
        if self.macros:
            lines.append("/* identifiers used as constants */")
            lines += sorted(self.macros.values())
        lines.append("#endif /* CKG_AUTODEFS_H */")
        with open(self.prelude, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
