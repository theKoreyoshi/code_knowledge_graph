"""compile_commands.json support (the most accurate source of parse flags)."""

from __future__ import annotations

import json
import os
import re


FLAGS_WITH_VALUE_TO_DROP = {
    "-o", "-MF", "-MT", "-MQ", "-MJ", "-c", "--serialize-diagnostics",
    "-fdiagnostics-format", "-dumpdir",
}
FLAGS_TO_DROP = {
    "-c", "-S", "-E", "-MD", "-MMD", "-MP", "-M", "-MM", "-MG", "-Winvalid-pch",
    "--save-temps", "-gsplit-dwarf", "-fno-pch-timestamp",
}


def load(path: str) -> dict[str, list[str]]:
    """Return ``{absolute source path: clang arguments}``."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            entries = json.load(fh)
    except (OSError, ValueError):
        return {}
    database: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        directory = entry.get("directory") or os.path.dirname(path)
        source = entry.get("file")
        if not source:
            continue
        arguments = entry.get("arguments")
        if arguments is None and isinstance(entry.get("command"), str):
            arguments = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', entry["command"])
        if not isinstance(arguments, list):
            continue
        cleaned = [str(a).strip('"') for a in arguments if str(a).strip()]
        if not os.path.isabs(source):
            source = os.path.join(directory, source)
        database[os.path.normcase(os.path.normpath(source))] = _strip(cleaned, directory)
    return database


def _strip(arguments: list[str], directory: str) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in FLAGS_WITH_VALUE_TO_DROP:
            index += 2
            continue
        if token in FLAGS_TO_DROP:
            index += 1
            continue
        if token.startswith("-o") and len(token) > 2:
            index += 1
            continue
        out.append(token)
        index += 1
    # make relative include paths absolute against the recorded directory
    fixed: list[str] = []
    index = 0
    while index < len(out):
        token = out[index]
        if token in ("-I", "-isystem", "-iquote", "-idirafter", "-include") and index + 1 < len(out):
            value = out[index + 1]
            if token != "-include" and not os.path.isabs(value):
                value = os.path.normpath(os.path.join(directory, value))
            fixed += [token, value]
            index += 2
            continue
        if token.startswith("-I") and len(token) > 2 and not os.path.isabs(token[2:]):
            fixed.append("-I" + os.path.normpath(os.path.join(directory, token[2:])))
        else:
            fixed.append(token)
        index += 1
    return fixed
