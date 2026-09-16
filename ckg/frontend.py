"""Convenience wrapper around :mod:`ckg.clang_capi`."""

from __future__ import annotations

import os
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_void_p
from dataclasses import dataclass, field

from . import clang_capi as capi
from .clang_capi import CXCursor, CXSourceLocation, CXSourceRange, CXToken, CXType, Clang


@dataclass(frozen=True)
class Pos:
    file: str
    line: int
    column: int

    def as_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "column": self.column}


@dataclass
class Token:
    spelling: str
    kind: int
    location: Pos
    offset: int = 0

    @property
    def is_identifier(self) -> bool:
        return self.kind == capi.CXToken_Identifier

    @property
    def is_punct(self) -> bool:
        return self.kind == capi.CXToken_Punctuation


def cursor_key(cursor: CXCursor) -> tuple:
    return (cursor.kind, cursor.xdata, cursor.data[0], cursor.data[1], cursor.data[2])


def _offset_of(lib, loc: CXSourceLocation) -> int:
    """Byte offset of a source location (file-relative)."""
    file_ptr = c_void_p()
    line = c_uint()
    col = c_uint()
    off = c_uint()
    lib.clang_getFileLocation(loc, byref(file_ptr), byref(line), byref(col), byref(off))
    return int(off.value)


_EMPTY_POS = Pos("", 0, 0)


class TranslationUnit:
    """A parsed translation unit with helper accessors."""

    def __init__(self, clang: Clang, tu, source: str) -> None:
        self.clang = clang
        self.lib = clang.lib
        self.tu = tu
        self.source = source
        self.root = self.lib.clang_getTranslationUnitCursor(tu)
        self._captured: list[tuple] = []
        self.errors: list[dict] = []

    # -- lifetime ----------------------------------------------------------
    def dispose(self) -> None:
        if self.tu:
            self.lib.clang_disposeTranslationUnit(self.tu)
            self.tu = None

    def __enter__(self) -> "TranslationUnit":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()

    # -- strings / types ---------------------------------------------------
    def spelling(self, cursor: CXCursor) -> str:
        return capi.cxstring_to_str(self.lib.clang_getCursorSpelling(cursor))

    def display_name(self, cursor: CXCursor) -> str:
        return capi.cxstring_to_str(self.lib.clang_getCursorDisplayName(cursor))

    def usr(self, cursor: CXCursor) -> str:
        return capi.cxstring_to_str(self.lib.clang_getCursorUSR(cursor))

    def type_spelling(self, ctype: CXType) -> str:
        return capi.cxstring_to_str(self.lib.clang_getTypeSpelling(ctype))

    def cursor_type(self, cursor: CXCursor) -> CXType:
        return self.lib.clang_getCursorType(cursor)

    # -- locations ---------------------------------------------------------
    def _loc(self, loc: CXSourceLocation, mode: str) -> Pos:
        fn = {
            "expansion": self.lib.clang_getExpansionLocation,
            "spelling": self.lib.clang_getSpellingLocation,
        }[mode]
        file_ptr = c_void_p()
        line = c_uint()
        col = c_uint()
        off = c_uint()
        fn(loc, byref(file_ptr), byref(line), byref(col), byref(off))
        name = ""
        if file_ptr:
            name = capi.cxstring_to_str(self.lib.clang_getFileName(file_ptr))
        return Pos(os.path.normpath(name) if name else "", int(line.value), int(col.value))

    def expansion_location(self, cursor: CXCursor) -> Pos:
        return self._loc(self.lib.clang_getCursorLocation(cursor), "expansion")

    def spelling_location(self, cursor: CXCursor) -> Pos:
        return self._loc(self.lib.clang_getCursorLocation(cursor), "spelling")

    def is_definition(self, cursor: CXCursor) -> bool:
        return bool(self.lib.clang_isCursorDefinition(cursor))

    def storage_class(self, cursor: CXCursor) -> str:
        return capi.STORAGE_CLASS_NAMES.get(self.lib.clang_Cursor_getStorageClass(cursor), "")

    # -- traversal ---------------------------------------------------------
    def walk(self, visit) -> None:
        """Depth-first walk; ``visit(cursor, parent, path)``.

        ``path`` is the list of ancestor cursors (root first, parent last).
        The call is O(1) in the number of libclang round-trips: libclang itself
        performs the recursion and hands us the parent cursor.
        """
        path: list[CXCursor] = [self.root]
        path_keys: list[tuple] = [cursor_key(self.root)]

        def _cb(cursor, parent, _data):
            pkey = cursor_key(parent)
            while len(path_keys) > 1 and path_keys[-1] != pkey:
                path_keys.pop()
                path.pop()
            try:
                visit(cursor, parent, path)
            except Exception as exc:  # a bad visitor must not abort the walk
                self.errors.append({"error": f"{type(exc).__name__}: {exc}"})
                if len(self.errors) > 40:
                    return capi.CXChildVisit_Break
            path.append(cursor)
            path_keys.append(cursor_key(cursor))
            return capi.CXChildVisit_Recurse

        callback = capi._CURSOR_VISITOR(_cb)
        self._captured.append(callback)  # keep alive for the duration of the call
        self.lib.clang_visitChildren(self.root, callback, None)
        self._captured.pop()

    # -- tokens ------------------------------------------------------------
    def tokens_in_range(self, extent: CXSourceRange, detailed: bool = False) -> list[Token]:
        """Tokens in a source range.

        ``detailed=False`` keeps only spelling/kind/offset: resolving a file and
        line for every token costs far more than the tokenisation itself and is
        only needed for macro work.
        """
        toks = POINTER(CXToken)()
        count = c_uint()
        self.lib.clang_tokenize(self.tu, extent, byref(toks), byref(count))
        out: list[Token] = []
        if not toks:
            return out
        try:
            lib = self.lib
            for i in range(count.value):
                token = toks[i]
                loc = lib.clang_getTokenLocation(self.tu, token)
                spelling = capi.cxstring_to_str(lib.clang_getTokenSpelling(self.tu, token))
                if detailed:
                    out.append(
                        Token(spelling, lib.clang_getTokenKind(token), self._loc(loc, "expansion"),
                              _offset_of(lib, loc))
                    )
                else:
                    out.append(
                        Token(spelling, lib.clang_getTokenKind(token), _EMPTY_POS, _offset_of(lib, loc))
                    )
        finally:
            self.lib.clang_disposeTokens(self.tu, toks, count)
        return out

    def tokens_of(self, cursor: CXCursor, detailed: bool = False) -> list[Token]:
        return self.tokens_in_range(self.lib.clang_getCursorExtent(cursor), detailed)

    # -- diagnostics -------------------------------------------------------
    def diagnostics(self) -> list[dict]:
        out = []
        n = self.lib.clang_getNumDiagnostics(self.tu)
        for i in range(n):
            diag = self.lib.clang_getDiagnostic(self.tu, i)
            severity = self.lib.clang_getDiagnosticSeverity(diag)
            text = capi.cxstring_to_str(self.lib.clang_formatDiagnostic(diag, 0))
            loc = self._loc(self.lib.clang_getDiagnosticLocation(diag), "expansion")
            self.lib.clang_disposeDiagnostic(diag)
            out.append(
                {
                    "severity": severity,
                    "severity_name": {
                        capi.CXDiagnostic_Ignored: "ignored",
                        capi.CXDiagnostic_Note: "note",
                        capi.CXDiagnostic_Warning: "warning",
                        capi.CXDiagnostic_Error: "error",
                        capi.CXDiagnostic_Fatal: "fatal",
                    }.get(severity, "unknown"),
                    "text": text,
                    "location": loc.as_dict(),
                }
            )
        return out

    # -- include graph -----------------------------------------------------
    def includes(self) -> list[dict]:
        """Return ``[{file, included_from}]`` edges as reported by clang."""
        edges: list[dict] = []
        lib = self.lib

        def _cb(included_file, stack, length, _data):
            included = capi.cxstring_to_str(lib.clang_getFileName(included_file)) if included_file else ""
            included = os.path.normpath(included) if included else ""
            origin = ""
            if length:
                last = stack[length - 1]
                fp = c_void_p()
                line = c_uint()
                col = c_uint()
                off = c_uint()
                lib.clang_getExpansionLocation(last, byref(fp), byref(line), byref(col), byref(off))
                if fp:
                    origin = os.path.normpath(capi.cxstring_to_str(lib.clang_getFileName(fp)))
            if included and origin:
                edges.append({"included": included, "from": origin})

        callback = capi._INCLUSION_VISITOR(_cb)
        self._captured.append(callback)
        lib.clang_getInclusions(self.tu, callback, None)
        self._captured.pop()
        return edges


def load_translation_unit(
    clang: Clang,
    source: str,
    args: list[str],
    *,
    skip_function_bodies: bool = False,
) -> TranslationUnit:
    """Parse ``source`` with the given clang command-line arguments."""
    lib = clang.lib
    argv = (c_char_p * len(args))(*[a.encode("utf-8") for a in args])
    options = capi.GLOBAL_OPTIONS
    if skip_function_bodies:
        options |= capi.CXTranslationUnit_SkipFunctionBodies
    tu = lib.clang_parseTranslationUnit(
        clang.thread_index(),
        os.fsencode(source),
        argv,
        len(args),
        None,
        0,
        options,
    )
    if not tu:
        raise capi.ClangError(f"clang failed to parse {source}")
    return TranslationUnit(clang, tu, source)
