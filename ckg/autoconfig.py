"""Work out how to parse a project without being told.

The toolchain looks for the same build metadata a human would: a
``compile_commands.json`` if one exists, otherwise CMake / Make / Meson /
Autotools files, and for firmware the IAR and Keil project files.  If nothing is
found it falls back to scanning the tree for header directories.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

from .languages import DEFAULT_EXTENSIONS, classify

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "out", "output", "outputs", "cmake-build-debug", "cmake-build-release",
    ".cache", ".idea", ".vs", "Debug", "Release",
}

SEARCH_COMPILE_COMMANDS = ("compile_commands.json", "build/compile_commands.json")

INCLUDE_HINT_NAMES = ("include", "inc", "api", "src", "source", "lib", "headers")


@dataclass
class Detection:
    source_root: str
    provider: str = "generic"
    evidence: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    std: str = ""
    target: str = ""
    languages: list[str] = field(default_factory=list)
    compile_commands: str = ""
    has_cpp: bool = False
    embedded: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_config_dict(self, output_dir: str = "./out") -> dict:
        data: dict = {
            "project_name": os.path.basename(os.path.normpath(self.source_root)),
            "source_root": self.source_root.replace("\\", "/"),
            "output_dir": output_dir,
            "include_dirs": [d.replace("\\", "/") for d in self.include_dirs],
            "std_shim_dirs": ["ckg/shims"],
            "shim_include_dirs": [],
            "defines": self.defines,
            "excludes": sorted(SKIP_DIRS),
            "std": self.std or ("gnu++17" if self.has_cpp else "gnu11"),
            "target": self.target,
            "tree_sitter": True,
            "preprocess": True,
            "auto_repair": True,
            "clang_driver": "",
            "compile_commands": self.compile_commands.replace("\\", "/") if self.compile_commands else "",
        }
        return data

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "evidence": self.evidence,
            "include_dirs": self.include_dirs,
            "defines": self.defines,
            "std": self.std,
            "target": self.target,
            "languages": self.languages,
            "embedded": self.embedded,
            "compile_commands": self.compile_commands,
            "warnings": self.warnings,
        }


def detect(source_root: str, max_header_dirs: int = 24) -> Detection:
    source_root = os.path.abspath(source_root)
    detection = Detection(source_root=source_root)

    languages: Counter = Counter()
    header_dirs: Counter = Counter()
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        depth = os.path.relpath(dirpath, source_root).count(os.sep)
        for name in filenames:
            language = classify(name)
            if language is None:
                continue
            languages[language.name] += 1
            if language.is_header:
                header_dirs[os.path.normpath(dirpath)] += 1

    detection.languages = [name for name, _ in languages.most_common()]
    detection.has_cpp = any(name.startswith("c++") for name in detection.languages)

    compile_commands = _find_compile_commands(source_root)
    if compile_commands:
        _from_compile_commands(detection, compile_commands)

    if not detection.include_dirs:
        _from_cmake(detection, source_root)
    if not detection.include_dirs:
        _from_make(detection, source_root)
    if not detection.include_dirs:
        _from_meson(detection, source_root)
    if not detection.include_dirs:
        _from_iar(detection, source_root)
    if not detection.include_dirs:
        _from_keil(detection, source_root)

    _detect_embedded(detection, source_root)

    _from_header_scan(detection, source_root, header_dirs, max_header_dirs)
    return detection


# ----------------------------------------------------------------------
def _find_compile_commands(source_root: str) -> str:
    for candidate in SEARCH_COMPILE_COMMANDS:
        path = os.path.join(source_root, *candidate.split("/"))
        if os.path.isfile(path):
            return path
    # one level of build directories, e.g. cmake-build-debug/
    for entry in sorted(os.listdir(source_root)):
        path = os.path.join(source_root, entry, "compile_commands.json")
        if os.path.isfile(path):
            return path
    return ""


def _from_compile_commands(detection: Detection, path: str) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            entries = json.load(fh)
    except (OSError, ValueError) as exc:
        detection.warnings.append(f"cannot read {path}: {exc}")
        return
    if not isinstance(entries, list):
        return
    includes: list[str] = []
    defines: list[str] = []
    std = ""
    target = ""
    base = os.path.dirname(path)
    for entry in entries[:4000]:
        if not isinstance(entry, dict):
            continue
        directory = entry.get("directory") or base
        command = entry.get("command")
        arguments = entry.get("arguments")
        if arguments is None and isinstance(command, str):
            arguments = _split_command(command)
        if not isinstance(arguments, list):
            continue
        index = 0
        while index < len(arguments):
            token = str(arguments[index])
            if token == "-I" and index + 1 < len(arguments):
                _push_path(includes, directory, arguments[index + 1])
                index += 2
                continue
            if token.startswith("-I") and len(token) > 2:
                _push_path(includes, directory, token[2:])
            elif token.startswith("-isystem") and len(token) > 8:
                _push_path(includes, directory, token[8:])
            elif token == "-D" and index + 1 < len(arguments):
                defines.append(str(arguments[index + 1]))
                index += 2
                continue
            elif token.startswith("-D") and len(token) > 2:
                defines.append(token[2:])
            elif token.startswith("-std="):
                std = token[5:]
            elif token == "--target" and index + 1 < len(arguments):
                target = str(arguments[index + 1])
                index += 2
                continue
            elif token.startswith("--target="):
                target = token.split("=", 1)[1]
            index += 1
    detection.provider = "compile_commands"
    detection.compile_commands = path
    detection.evidence.append(f"compile_commands.json: {len(entries)} entries")
    detection.include_dirs = _dedupe(includes)
    detection.defines = _dedupe(defines)[:64]
    detection.std = std
    detection.target = target


def _split_command(command: str) -> list[str]:
    return re.findall(r'"[^"]*"|\'[^\']*\'|\S+', command)


def _push_path(bucket: list[str], base: str, value: str) -> None:
    value = str(value).strip().strip('"')
    if not value or value.startswith("$") or value.startswith("-"):
        return
    if not os.path.isabs(value):
        value = os.path.normpath(os.path.join(base, value))
    if os.path.isdir(value):
        bucket.append(os.path.normpath(value))


# ----------------------------------------------------------------------
def _from_cmake(detection: Detection, source_root: str) -> None:
    files = [
        os.path.join(source_root, "CMakeLists.txt"),
        os.path.join(source_root, "cmake", "CMakeLists.txt"),
    ]
    existing = [f for f in files if os.path.isfile(f)]
    if not existing:
        return
    detection.evidence.append("CMakeLists.txt")
    include_dirs: list[str] = []
    defines: list[str] = []
    std = ""
    for path in existing:
        text = _read(path)
        base = os.path.dirname(path)
        for match in re.finditer(
            r"(?:include_directories|target_include_directories)\s*\(([^)]*)\)", text, re.S
        ):
            for token in _cmake_tokens(match.group(1)):
                if token.upper() in ("PRIVATE", "PUBLIC", "INTERFACE"):
                    continue
                _push_path(include_dirs, base, token)
        for match in re.finditer(r"(?:add_definitions|target_compile_definitions)\s*\(([^)]*)\)", text, re.S):
            for token in _cmake_tokens(match.group(1)):
                if token.startswith("-D"):
                    defines.append(token[2:])
                elif token.startswith("-std="):
                    std = token[5:]
        for match in re.finditer(r"CMAKE_CXX?_STANDARD\s+(\d+)", text):
            std = "c++" + match.group(1) if "CXX" in match.group(0) else "c" + match.group(1)
    if include_dirs or defines:
        detection.provider = "cmake"
        detection.include_dirs = _dedupe(include_dirs)
        detection.defines = _dedupe(defines)[:64]
        detection.std = detection.std or std


def _cmake_tokens(text: str) -> list[str]:
    tokens = []
    for raw in re.split(r"[\s\n]+", text):
        token = raw.strip().strip('"')
        if token and not token.startswith("${") and token not in ("", ")"):
            tokens.append(token)
    return tokens


# ----------------------------------------------------------------------
def _from_make(detection: Detection, source_root: str) -> None:
    candidates = [
        "Makefile", "makefile", "GNUmakefile",
        os.path.join("src", "Makefile"), os.path.join("lib", "Makefile"),
    ]
    existing = [os.path.join(source_root, c) for c in candidates]
    existing = [f for f in existing if os.path.isfile(f)]
    if not existing:
        return
    detection.evidence.append("Makefile")
    include_dirs: list[str] = []
    defines: list[str] = []
    std = ""
    for path in existing:
        base = os.path.dirname(path)
        text = _read(path)
        variables = {}
        for match in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:+!]?=\s*(.*?)\s*$", text, re.M):
            variables.setdefault(match.group(1), match.group(2))
        blob = "\n".join(
            variables.get(name, "")
            for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "ALL_CFLAGS", "ALL_CXXFLAGS", "DEFS", "INCLUDES")
        )
        blob += "\n" + text
        for token in blob.split():
            if token.startswith("-I") and len(token) > 2:
                _push_path(include_dirs, base, token[2:])
            elif token.startswith("-D") and len(token) > 2:
                defines.append(token[2:])
            elif token.startswith("-std="):
                std = token[5:]
    if include_dirs or defines:
        detection.provider = "make"
        detection.include_dirs = _dedupe(include_dirs)
        detection.defines = _dedupe(defines)[:64]
        detection.std = detection.std or std


# ----------------------------------------------------------------------
def _from_meson(detection: Detection, source_root: str) -> None:
    path = os.path.join(source_root, "meson.build")
    if not os.path.isfile(path):
        return
    detection.evidence.append("meson.build")
    text = _read(path)
    include_dirs: list[str] = []
    defines: list[str] = []
    for match in re.finditer(r"include_directories\s*\(([^)]*)\)", text):
        for token in re.findall(r"'([^']+)'", match.group(1)):
            _push_path(include_dirs, source_root, token)
    for match in re.finditer(r"add_project_arguments\s*\(([^)]*)\)", text):
        for token in re.findall(r"'-D([^']+)'", match.group(1)):
            defines.append(token)
    if include_dirs or defines:
        detection.provider = "meson"
        detection.include_dirs = _dedupe(include_dirs)
        detection.defines = _dedupe(defines)[:64]


# ----------------------------------------------------------------------
def _from_iar(detection: Detection, source_root: str) -> None:
    projects = []
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith((".ewp", ".ewd", ".eww")):
                projects.append(os.path.join(dirpath, name))
    if not projects:
        return
    detection.evidence.append("IAR project: " + ", ".join(os.path.basename(p) for p in projects[:3]))
    includes: list[str] = []
    defines: list[str] = []
    for path in projects:
        text = _read(path)
        project_dir = os.path.dirname(path)
        for match in re.finditer(r"<name>\s*([A-Za-z0-9_]*[Ii]nclude[Pp]ath[A-Za-z0-9_]*)\s*</name>(.*?)</option>", text, re.S):
            for value in re.findall(r"<state>\s*([^<]+?)\s*</state>", match.group(2)):
                resolved = _resolve_iar_path(value, project_dir, source_root)
                if resolved:
                    includes.append(resolved)
        for match in re.finditer(r"<name>\s*([A-Za-z0-9_]*[Dd]efines?[A-Za-z0-9_]*)\s*</name>(.*?)</option>", text, re.S):
            for value in re.findall(r"<state>\s*([^<]+?)\s*</state>", match.group(2)):
                for define in re.split(r"[;,\s]+", value.strip()):
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(=|$)", define):
                        defines.append(define)
    if includes or defines:
        detection.provider = "iar"
        detection.include_dirs = _dedupe(includes)
        detection.defines = _dedupe(defines)[:64]


def _resolve_iar_path(value: str, project_dir: str, source_root: str) -> str:
    value = value.strip()
    if not value or value.startswith("<"):
        return ""
    value = (
        value.replace("$PROJ_DIR$", project_dir)
        .replace("$PROJ_DIR", project_dir)
        .replace("$TOOLKIT_DIR$", "")
        .replace("$EW_DIR$", "")
    )
    if not os.path.isabs(value):
        value = os.path.join(source_root, value)
    value = os.path.normpath(value)
    return value if os.path.isdir(value) else ""


# ----------------------------------------------------------------------
def _from_keil(detection: Detection, source_root: str) -> None:
    projects = []
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith((".uvprojx", ".uvproj", ".uvoptx")):
                projects.append(os.path.join(dirpath, name))
    if not projects:
        return
    detection.evidence.append("Keil project: " + ", ".join(os.path.basename(p) for p in projects[:3]))
    includes: list[str] = []
    defines: list[str] = []
    for path in projects:
        project_dir = os.path.dirname(path)
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        root = tree.getroot()
        for element in root.iter():
            tag = element.tag.split("}")[-1]
            text = (element.text or "").strip()
            if not text:
                continue
            if tag == "IncludePath":
                for entry in text.split(";"):
                    entry = entry.strip()
                    if not entry:
                        continue
                    resolved = os.path.normpath(os.path.join(project_dir, entry))
                    if os.path.isdir(resolved):
                        includes.append(resolved)
            elif tag == "Define":
                for entry in re.split(r"[,\s]+", text):
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(=|$)", entry):
                        defines.append(entry)
    if includes or defines:
        detection.provider = "keil"
        detection.include_dirs = _dedupe(includes)
        detection.defines = _dedupe(defines)[:64]


# ----------------------------------------------------------------------
def _detect_embedded(detection: Detection, source_root: str) -> None:
    markers = (".ewp", ".uvprojx", ".icf", ".sct", ".ld", ".lds", ".startup", "startup_", "FreeRTOSConfig.h")
    hits: list[str] = []
    text_hits: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames[:400]:
            lower = name.lower()
            if any(marker in lower for marker in markers):
                hits.append(name)
            if scanned < 120 and lower.endswith((".c", ".h", ".cpp", ".hpp", ".s", ".asm", ".icf", ".sct")):
                scanned += 1
                text = _read(os.path.join(dirpath, name))[:60000]
                for token in EMBEDDED_TOKENS:
                    if token in text and token not in text_hits:
                        text_hits.append(token)
        if len(hits) > 12:
            break
    if hits or text_hits:
        detection.embedded = True
        evidence = sorted(set(hits))[:4] + text_hits[:4]
        detection.evidence.append("embedded markers: " + ", ".join(evidence))
        if not detection.target:
            detection.target = "arm-none-eabi"


#: strings that reliably indicate a bare-metal / RTOS firmware build
EMBEDDED_TOKENS = (
    "__ICCARM__", "__IAR_SYSTEMS_ICC__", "__CC_ARM", "__ARMCC_VERSION",
    "__arm__", "IRQHandler", "NVIC_", "LL_", "HAL_Init", "FreeRTOS",
    "__irq", "__no_init", "__packed struct", "#pragma vector",
)


def _from_header_scan(
    detection: Detection, source_root: str, header_dirs: Counter, limit: int
) -> None:
    """Fallback: treat directories that contain headers as include paths."""
    scored = []
    for directory, count in header_dirs.items():
        rel = os.path.relpath(directory, source_root).replace("\\", "/")
        depth = 0 if rel == "." else rel.count("/") + 1
        bonus = 3 if os.path.basename(directory).lower() in INCLUDE_HINT_NAMES else 0
        scored.append((bonus - depth, count, directory))
    scored.sort(reverse=True)
    for _, _, directory in scored[:limit]:
        if directory not in detection.include_dirs:
            detection.include_dirs.append(directory)
    if not detection.evidence:
        detection.provider = "scan"
        detection.evidence.append(
            f"no build files found; using {len(detection.include_dirs)} header directories"
        )


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _dedupe(values) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
