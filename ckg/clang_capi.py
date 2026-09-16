"""Thin ctypes binding for the subset of libclang's C API used by the extractor.

``clang.cindex`` (the official Python binding shipped with the ``libclang``
wheel) hides a few APIs the knowledge-graph extractor needs -- most notably
macro-expansion cursors, ``clang_Cursor_isMacroFunctionLike`` and the
spelling/expansion location split that reveals macro-generated declarations.
Those functions are part of libclang itself, so we call them directly.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_uint, c_void_p

# --------------------------------------------------------------------------
# struct layouts (must match clang-c/Index.h exactly)
# --------------------------------------------------------------------------


class CXCursor(Structure):
    _fields_ = [
        ("kind", c_int),
        ("xdata", c_int),
        ("data", c_void_p * 3),
    ]


class CXType(Structure):
    _fields_ = [
        ("kind", c_int),
        ("data", c_void_p * 2),
    ]


class CXString(Structure):
    _fields_ = [
        ("data", c_void_p),
        ("private_flags", c_uint),
    ]


class CXSourceLocation(Structure):
    _fields_ = [
        ("ptr_data", c_void_p * 2),
        ("int_data", c_uint),
    ]


class CXSourceRange(Structure):
    _fields_ = [
        ("ptr_data", c_void_p * 2),
        ("begin_int_data", c_uint),
        ("end_int_data", c_uint),
    ]


class CXToken(Structure):
    _fields_ = [
        ("int_data", c_uint * 4),
        ("ptr_data", c_void_p),
    ]


# --------------------------------------------------------------------------
# cursor / type kind identifiers
# --------------------------------------------------------------------------

CXCursor_StructDecl = 2
CXCursor_UnionDecl = 3
CXCursor_ClassDecl = 4
CXCursor_EnumDecl = 5
CXCursor_FieldDecl = 6
CXCursor_EnumConstantDecl = 7
CXCursor_FunctionDecl = 8
CXCursor_VarDecl = 9
CXCursor_ParmDecl = 10
CXCursor_TypedefDecl = 20
CXCursor_CXXMethod = 21
CXCursor_Namespace = 22
CXCursor_LinkageSpec = 23
CXCursor_Constructor = 24
CXCursor_Destructor = 25
CXCursor_ConversionFunction = 26
CXCursor_TemplateTypeParameter = 27
CXCursor_FunctionTemplate = 30
CXCursor_ClassTemplate = 31
CXCursor_ClassTemplatePartialSpecialization = 32
CXCursor_NamespaceAlias = 33
CXCursor_UsingDirective = 34
CXCursor_UsingDeclaration = 35
CXCursor_TypeAliasDecl = 36
CXCursor_CXXBaseSpecifier = 44
CXCursor_OverloadedDeclRef = 49
CXCursor_LambdaExpr = 144
CXCursor_LinkageSpec = 23
CXCursor_Constructor = 24
CXCursor_Destructor = 25
CXCursor_ConversionFunction = 26
CXCursor_FunctionTemplate = 30
CXCursor_NamespaceAlias = 33
CXCursor_MacroDefinition = 501
CXCursor_MacroInstantiation = 502
CXCursor_MacroExpansion = 502
CXCursor_InclusionDirective = 503

CXCursor_LastDecl = 500

# --- statements (CXCursorKind 200..) --------------------------------------
CXCursor_UnexposedStmt = 200
CXCursor_LabelStmt = 201
CXCursor_CompoundStmt = 202
CXCursor_CaseStmt = 203
CXCursor_DefaultStmt = 204
CXCursor_IfStmt = 205
CXCursor_SwitchStmt = 206
CXCursor_WhileStmt = 207
CXCursor_DoStmt = 208
CXCursor_ForStmt = 209
CXCursor_GotoStmt = 210
CXCursor_IndirectGotoStmt = 211
CXCursor_ContinueStmt = 212
CXCursor_BreakStmt = 213
CXCursor_ReturnStmt = 214
CXCursor_DeclStmt = 230

# --- expressions (CXCursorKind 100..) -------------------------------------
CXCursor_UnexposedExpr = 100
CXCursor_DeclRefExpr = 101
CXCursor_MemberRefExpr = 102
CXCursor_CallExpr = 103
CXCursor_ObjCMessageExpr = 104
CXCursor_BlockExpr = 105
CXCursor_IntegerLiteral = 106
CXCursor_FloatingLiteral = 107
CXCursor_ImaginaryLiteral = 108
CXCursor_StringLiteral = 109
CXCursor_CharacterLiteral = 110
CXCursor_ParenExpr = 111
CXCursor_UnaryOperator = 112
CXCursor_ArraySubscriptExpr = 113
CXCursor_BinaryOperator = 114
CXCursor_CompoundAssignOperator = 115
CXCursor_ConditionalOperator = 116
CXCursor_CStyleCastExpr = 117
CXCursor_CompoundLiteralExpr = 118
CXCursor_InitListExpr = 119
CXCursor_DesignatedInitExpr = 125

CXType_Invalid = 0
CXType_Unexposed = 1
CXType_Void = 2
CXType_Pointer = 103
CXType_ConstantArray = 112
CXType_IncompleteArray = 114
CXType_FunctionProto = 111
CXType_FunctionNoProto = 110
CXType_Elaborated = 119
CXType_Typedef = 120
CXType_Record = 105
CXType_Enum = 106

CXToken_Punctuation = 0
CXToken_Keyword = 1
CXToken_Identifier = 2
CXToken_Literal = 3
CXToken_Comment = 4

CXTranslationUnit_DetailedPreprocessingRecord = 0x01
CXTranslationUnit_Incomplete = 0x02
CXTranslationUnit_PrecompiledPreamble = 0x04
CXTranslationUnit_CacheCompletionResults = 0x08
CXTranslationUnit_ForSerialization = 0x10
CXTranslationUnit_SkipFunctionBodies = 0x40
CXTranslationUnit_KeepGoing = 0x200
CXTranslationUnit_SingleFileParse = 0x400

CXChildVisit_Break = 0
CXChildVisit_Continue = 1
CXChildVisit_Recurse = 2

CXDiagnostic_Ignored = 0
CXDiagnostic_Note = 1
CXDiagnostic_Warning = 2
CXDiagnostic_Error = 3
CXDiagnostic_Fatal = 4

CX_SC_Invalid = 0
CX_SC_None = 1
CX_SC_Extern = 2
CX_SC_Static = 3
CX_SC_PrivateExtern = 4
CX_SC_OpenCLWorkGroupLocal = 5
CX_SC_Auto = 6
CX_SC_Register = 7

STORAGE_CLASS_NAMES = {
    CX_SC_Invalid: "",
    CX_SC_None: "",
    CX_SC_Extern: "extern",
    CX_SC_Static: "static",
    CX_SC_PrivateExtern: "static",
    CX_SC_OpenCLWorkGroupLocal: "",
    CX_SC_Auto: "auto",
    CX_SC_Register: "register",
}


# --------------------------------------------------------------------------
# library loading
# --------------------------------------------------------------------------


def _candidate_library_paths() -> list[str]:
    env = os.environ.get("LIBCLANG_PATH")
    paths: list[str] = []
    if env:
        paths.append(os.path.join(env, "libclang.dll") if os.path.isdir(env) else env)
    try:
        import clang.cindex as cindex  # noqa: PLC0415

        native = getattr(cindex.Config, "library_path", None)
        libfile = getattr(cindex.Config, "library_file", None)
        if native and libfile:
            paths.append(os.path.join(native, libfile))
        if native and os.path.isdir(native):
            # the wheel ships libclang.dll / libclang.so without advertising it
            for entry in sorted(os.listdir(native)):
                if entry.lower().startswith("libclang") and entry.lower().endswith((".dll", ".so", ".dylib", ".so.1")):
                    paths.append(os.path.join(native, entry))
    except Exception:  # pragma: no cover - optional dependency
        pass
    if sys.platform == "win32":
        paths += ["libclang.dll", r"C:\Program Files\LLVM\bin\libclang.dll"]
    elif sys.platform == "darwin":
        paths += ["libclang.dylib", "/Library/Developer/CommandLineTools/usr/lib/libclang.dylib"]
    else:
        paths += ["libclang.so", "libclang-18.so", "libclang-17.so", "libclang-16.so"]
    return paths


class ClangError(RuntimeError):
    pass


_CURSOR_VISITOR = ctypes.CFUNCTYPE(c_int, CXCursor, CXCursor, c_void_p)
_INCLUSION_VISITOR = ctypes.CFUNCTYPE(None, c_void_p, POINTER(CXSourceLocation), c_uint, c_void_p)

_LOADED: "Clang | None" = None


class Clang:
    """Loaded libclang library with the signatures we rely on."""

    _instance: "Clang | None" = None

    def __init__(self, path: str | None = None) -> None:
        global _LOADED
        resolved = None
        for cand in ([path] if path else []) + _candidate_library_paths():
            if not cand:
                continue
            try:
                self.lib = ctypes.CDLL(cand)
                resolved = cand
                break
            except OSError:
                continue
        if resolved is None:
            raise ClangError(
                "Could not load libclang. Install it with `pip install libclang` "
                "or set LIBCLANG_PATH to a directory containing libclang."
            )
        self.path = resolved
        self._declare()
        self._cursor_visitor_refs: list[object] = []
        self.index = self.lib.clang_createIndex(0, 0)
        self._tls = threading.local()
        _LOADED = self

    def new_index(self):
        """Create a fresh ``CXIndex`` (useful when parsing many units)."""
        return self.lib.clang_createIndex(0, 0)

    def thread_index(self):
        """A ``CXIndex`` private to the calling thread.

        Parsing several translation units at once is safe as long as each
        thread owns its index, which is what lets the extractor use a thread
        pool without copying the whole library.
        """
        index = getattr(self._tls, "index", None)
        if index is None:
            index = self.lib.clang_createIndex(0, 0)
            self._tls.index = index
        return index

    # -- singleton helper --------------------------------------------------
    @classmethod
    def instance(cls, path: str | None = None) -> "Clang":
        if cls._instance is None:
            cls._instance = cls(path)
        return cls._instance

    @property
    def version(self) -> str:
        return cxstring_to_str(self.lib.clang_getClangVersion())

    def _declare(self) -> None:
        L = self.lib

        L.clang_getClangVersion.restype = CXString

        L.clang_createIndex.argtypes = [c_int, c_int]
        L.clang_createIndex.restype = c_void_p
        L.clang_disposeIndex.argtypes = [c_void_p]

        L.clang_parseTranslationUnit.argtypes = [
            c_void_p,
            c_char_p,
            POINTER(c_char_p),
            c_int,
            c_void_p,
            c_uint,
            c_uint,
        ]
        L.clang_parseTranslationUnit.restype = c_void_p
        L.clang_disposeTranslationUnit.argtypes = [c_void_p]

        L.clang_getTranslationUnitCursor.argtypes = [c_void_p]
        L.clang_getTranslationUnitCursor.restype = CXCursor

        L.clang_visitChildren.argtypes = [CXCursor, _CURSOR_VISITOR, c_void_p]
        L.clang_visitChildren.restype = c_uint

        L.clang_getCursorKind.argtypes = [CXCursor]
        L.clang_getCursorKind.restype = c_int
        L.clang_getCursorSpelling.argtypes = [CXCursor]
        L.clang_getCursorSpelling.restype = CXString
        L.clang_getCursorDisplayName.argtypes = [CXCursor]
        L.clang_getCursorDisplayName.restype = CXString
        L.clang_getCursorUSR.argtypes = [CXCursor]
        L.clang_getCursorUSR.restype = CXString
        L.clang_getCursorLocation.argtypes = [CXCursor]
        L.clang_getCursorLocation.restype = CXSourceLocation
        L.clang_getCursorExtent.argtypes = [CXCursor]
        L.clang_getCursorExtent.restype = CXSourceRange
        L.clang_getRangeStart.argtypes = [CXSourceRange]
        L.clang_getRangeStart.restype = CXSourceLocation
        L.clang_getRangeEnd.argtypes = [CXSourceRange]
        L.clang_getRangeEnd.restype = CXSourceLocation
        L.clang_getCursorType.argtypes = [CXCursor]
        L.clang_getCursorType.restype = CXType
        L.clang_getCursorReferenced.argtypes = [CXCursor]
        L.clang_getCursorReferenced.restype = CXCursor
        L.clang_getCursorDefinition.argtypes = [CXCursor]
        L.clang_getCursorDefinition.restype = CXCursor
        L.clang_getCursorSemanticParent.argtypes = [CXCursor]
        L.clang_getCursorSemanticParent.restype = CXCursor
        L.clang_getCursorLexicalParent.argtypes = [CXCursor]
        L.clang_getCursorLexicalParent.restype = CXCursor
        try:
            L.clang_Cursor_getTranslationUnit.argtypes = [CXCursor]
            L.clang_Cursor_getTranslationUnit.restype = c_void_p
        except AttributeError:
            pass

        L.clang_Cursor_isNull.argtypes = [CXCursor]
        L.clang_Cursor_isNull.restype = c_int
        L.clang_isCursorDefinition.argtypes = [CXCursor]
        L.clang_isCursorDefinition.restype = c_uint
        L.clang_Cursor_isMacroFunctionLike.argtypes = [CXCursor]
        L.clang_Cursor_isMacroFunctionLike.restype = c_uint
        L.clang_Cursor_isMacroBuiltin.argtypes = [CXCursor]
        L.clang_Cursor_isMacroBuiltin.restype = c_uint
        L.clang_Cursor_getNumArguments.argtypes = [CXCursor]
        L.clang_Cursor_getNumArguments.restype = c_int
        L.clang_Cursor_getArgument.argtypes = [CXCursor, c_uint]
        L.clang_Cursor_getArgument.restype = CXCursor
        L.clang_Cursor_getStorageClass.argtypes = [CXCursor]
        L.clang_Cursor_getStorageClass.restype = c_int
        L.clang_equalCursors.argtypes = [CXCursor, CXCursor]
        L.clang_equalCursors.restype = c_uint
        L.clang_hashCursor.argtypes = [CXCursor]
        L.clang_hashCursor.restype = c_uint

        L.clang_getTypeSpelling.argtypes = [CXType]
        L.clang_getTypeSpelling.restype = CXString
        L.clang_getCanonicalType.argtypes = [CXType]
        L.clang_getCanonicalType.restype = CXType
        L.clang_getTypeDeclaration.argtypes = [CXType]
        L.clang_getTypeDeclaration.restype = CXCursor
        L.clang_getPointeeType.argtypes = [CXType]
        L.clang_getPointeeType.restype = CXType
        L.clang_getResultType.argtypes = [CXType]
        L.clang_getResultType.restype = CXType
        L.clang_getNumArgTypes.argtypes = [CXType]
        L.clang_getNumArgTypes.restype = c_int
        L.clang_getArgType.argtypes = [CXType, c_uint]
        L.clang_getArgType.restype = CXType
        L.clang_getArrayElementType.argtypes = [CXType]
        L.clang_getArrayElementType.restype = CXType
        L.clang_getArraySize.argtypes = [CXType]
        L.clang_getArraySize.restype = ctypes.c_longlong
        L.clang_getTypedefDeclUnderlyingType.argtypes = [CXCursor]
        L.clang_getTypedefDeclUnderlyingType.restype = CXType
        L.clang_getEnumDeclIntegerType.argtypes = [CXCursor]
        L.clang_getEnumDeclIntegerType.restype = CXType
        L.clang_getEnumConstantDeclValue.argtypes = [CXCursor]
        L.clang_getEnumConstantDeclValue.restype = ctypes.c_longlong

        L.clang_getFileName.argtypes = [c_void_p]
        L.clang_getFileName.restype = CXString
        L.clang_getExpansionLocation.argtypes = [
            CXSourceLocation,
            POINTER(c_void_p),
            POINTER(c_uint),
            POINTER(c_uint),
            POINTER(c_uint),
        ]
        L.clang_getSpellingLocation.argtypes = [
            CXSourceLocation,
            POINTER(c_void_p),
            POINTER(c_uint),
            POINTER(c_uint),
            POINTER(c_uint),
        ]
        L.clang_getPresumedLocation.argtypes = [
            CXSourceLocation,
            POINTER(CXString),
            POINTER(c_uint),
            POINTER(c_uint),
        ]
        L.clang_getFileLocation.argtypes = [
            CXSourceLocation,
            POINTER(c_void_p),
            POINTER(c_uint),
            POINTER(c_uint),
            POINTER(c_uint),
        ]
        L.clang_Location_isFromMainFile.argtypes = [CXSourceLocation]
        L.clang_Location_isFromMainFile.restype = c_int
        L.clang_Location_isInSystemHeader.argtypes = [CXSourceLocation]
        L.clang_Location_isInSystemHeader.restype = c_int

        L.clang_tokenize.argtypes = [c_void_p, CXSourceRange, POINTER(POINTER(CXToken)), POINTER(c_uint)]
        L.clang_disposeTokens.argtypes = [c_void_p, POINTER(CXToken), c_uint]
        L.clang_getTokenSpelling.argtypes = [c_void_p, CXToken]
        L.clang_getTokenSpelling.restype = CXString
        L.clang_getTokenKind.argtypes = [CXToken]
        L.clang_getTokenKind.restype = c_int
        L.clang_getTokenLocation.argtypes = [c_void_p, CXToken]
        L.clang_getTokenLocation.restype = CXSourceLocation
        L.clang_getTokenExtent.argtypes = [c_void_p, CXToken]
        L.clang_getTokenExtent.restype = CXSourceRange

        L.clang_getNumDiagnostics.argtypes = [c_void_p]
        L.clang_getNumDiagnostics.restype = c_uint
        L.clang_getDiagnostic.argtypes = [c_void_p, c_uint]
        L.clang_getDiagnostic.restype = c_void_p
        L.clang_getDiagnosticSeverity.argtypes = [c_void_p]
        L.clang_getDiagnosticSeverity.restype = c_int
        L.clang_formatDiagnostic.argtypes = [c_void_p, c_uint]
        L.clang_formatDiagnostic.restype = CXString
        L.clang_getDiagnosticLocation.argtypes = [c_void_p]
        L.clang_getDiagnosticLocation.restype = CXSourceLocation
        L.clang_disposeDiagnostic.argtypes = [c_void_p]

        L.clang_getInclusions.argtypes = [c_void_p, _INCLUSION_VISITOR, c_void_p]
        L.clang_getInclusions.restype = None

        L.clang_getCString.argtypes = [CXString]
        L.clang_getCString.restype = c_char_p
        L.clang_disposeString.argtypes = [CXString]


def cxstring_to_str(value: CXString) -> str:
    """Convert a CXString to a Python string, releasing the native buffer."""
    if _LOADED is None:
        raise ClangError("libclang has not been initialised yet")
    lib = _LOADED.lib
    raw = lib.clang_getCString(value)
    text = raw.decode("utf-8", "replace") if raw else ""
    lib.clang_disposeString(value)
    return text


GLOBAL_OPTIONS = (
    CXTranslationUnit_DetailedPreprocessingRecord
    | CXTranslationUnit_KeepGoing
)
