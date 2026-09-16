"""clang/libclang based entity + relation extraction for C projects.

The extractor walks clang's *semantic* AST rather than the textual syntax tree.
That is what makes variable-level facts, resolved types, macro expansion sites
and the spelling/expansion location split (used to detect declarations produced
by the preprocessor) available at all.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

from . import clang_capi as capi
from .clang_capi import CXCursor
from .frontend import TranslationUnit, cursor_key, load_translation_unit, _offset_of
from .macro_engine import Expansion, MacroDef, MacroExpander, parse_define, split_definition

TYPE_DECL_KINDS = {
    capi.CXCursor_StructDecl,
    capi.CXCursor_UnionDecl,
    capi.CXCursor_ClassDecl,
    capi.CXCursor_EnumDecl,
    capi.CXCursor_TypedefDecl,
}

DECL_KIND_TO_ENTITY = {
    capi.CXCursor_FunctionDecl: "FUNCTION",
    capi.CXCursor_FunctionTemplate: "FUNCTION",
    capi.CXCursor_CXXMethod: "METHOD",
    capi.CXCursor_Constructor: "METHOD",
    capi.CXCursor_Destructor: "METHOD",
    capi.CXCursor_ConversionFunction: "METHOD",
    capi.CXCursor_VarDecl: "VARIABLE",
    capi.CXCursor_ParmDecl: "PARAMETER",
    capi.CXCursor_FieldDecl: "FIELD",
    capi.CXCursor_EnumConstantDecl: "ENUM_CONST",
    capi.CXCursor_StructDecl: "STRUCT",
    capi.CXCursor_UnionDecl: "UNION",
    capi.CXCursor_ClassDecl: "CLASS",
    capi.CXCursor_ClassTemplate: "CLASS",
    capi.CXCursor_ClassTemplatePartialSpecialization: "CLASS",
    capi.CXCursor_EnumDecl: "ENUM",
    capi.CXCursor_TypedefDecl: "TYPEDEF",
    capi.CXCursor_TypeAliasDecl: "TYPEDEF",
    capi.CXCursor_Namespace: "NAMESPACE",
    capi.CXCursor_NamespaceAlias: "NAMESPACE",
    capi.CXCursor_MacroDefinition: "MACRO",
}

#: declarations that behave like functions/free functions
FUNCTION_LIKE_KINDS = {
    capi.CXCursor_FunctionDecl,
    capi.CXCursor_FunctionTemplate,
    capi.CXCursor_CXXMethod,
    capi.CXCursor_Constructor,
    capi.CXCursor_Destructor,
    capi.CXCursor_ConversionFunction,
}

#: declarations that define a named type
TYPE_LIKE_KINDS = {
    capi.CXCursor_StructDecl,
    capi.CXCursor_UnionDecl,
    capi.CXCursor_ClassDecl,
    capi.CXCursor_ClassTemplate,
    capi.CXCursor_ClassTemplatePartialSpecialization,
    capi.CXCursor_EnumDecl,
}

ASSIGN_OPERATORS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
COMPOUND_ASSIGN = ASSIGN_OPERATORS - {"="}
OPERATOR_TOKENS = ASSIGN_OPERATORS | {
    "+", "-", "*", "/", "%", "&&", "||", "==", "!=", "<", ">", "<=", ">=",
    "&", "|", "^", "?", "++", "--",
}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _is_pointer_type(ctype) -> bool:
    return ctype.kind == capi.CXType_Pointer


def _quality(errors: int, entities: int) -> str:
    """How much of a translation unit could actually be understood."""
    if errors == 0:
        return "full"
    if entities and errors <= max(3, entities * 0.25):
        return "partial"
    return "degraded"


class ClangExtractor:
    def __init__(self, graph, config, log=print, args_builder=None) -> None:
        self.graph = graph
        self.config = config
        self.log = log
        self.args_builder = args_builder
        self.root = _norm(config.source_root)
        self.macro_defs: dict[str, MacroDef] = {}
        self.macro_def_locations: dict[str, list[MacroDef]] = defaultdict(list)
        self.expansions: list = []
        self.pointer_assignments: dict[str, set] = defaultdict(set)
        self.diagnostics: list[dict] = []
        self.files_parsed: list[dict] = []
        self.functions_defined: set[str] = set()
        self._post_processed_macros: set[str] = set()
        self._post_processed_expansions = 0
        self._seen_edges: set[tuple] = set()
        self._key_to_id: dict[tuple, str] = {}
        self._binop_cache: dict[tuple, tuple] = {}
        self._unop_cache: dict[tuple, str] = {}
        self._lhs_of: dict[tuple, str] = {}
        self._lhs_priority = 1
        self._file_bytes_cache: dict[str, bytes] = {}
        self._file_lines: dict[str, list[str]] = {}
        self.touched_files: set[str] = set()
        self._expansion_index: dict[tuple, list] = defaultdict(list)
        self.relevant_roots = self._collect_relevant_roots()
        self._system_include_counts: dict[str, int] = {}
        self._tu: TranslationUnit | None = None

    # ==================================================================
    # small helpers
    # ==================================================================
    def rel_path(self, path: str) -> str:
        if not path:
            return ""
        path = os.path.normpath(path)
        if self.root and _norm(path).startswith(self.root):
            return os.path.relpath(path, self.config.source_root).replace("\\", "/")
        return path.replace("\\", "/")

    def is_project_file(self, path: str) -> bool:
        return bool(path) and _norm(path).startswith(self.root)

    def _collect_relevant_roots(self) -> list[str]:
        """Directories whose contents become entities in their own right.

        Everything else (the C library, SDK headers) only shows up when the
        project code actually references something from it, which keeps large
        builds from drowning in declarations nobody wrote.
        """
        roots = [self.root]
        config = self.config
        self.excluded_roots = [
            _norm(getattr(config, "output_dir", "") or "")
        ]
        for directory in (
            list(config.include_dirs) + list(config.shim_include_dirs)
        ):
            if directory:
                roots.append(_norm(directory))
        repair_dir = getattr(config, "_auto_shim_dir", "")
        if repair_dir:
            roots.append(_norm(repair_dir))
        return roots

    def is_relevant_file(self, path: str) -> bool:
        if not path:
            return False
        norm = _norm(path)
        for excluded in getattr(self, "excluded_roots", []):
            if excluded and norm.startswith(excluded):
                return False
        if "code-kg-tool" in norm.replace("\\", "/").rsplit("/", 1)[0] and "/_auto_shim/" in norm.replace("\\", "/"):
            return True
        return any(norm.startswith(prefix) for prefix in self.relevant_roots)

    def source_lines(self, path: str) -> list[str]:
        key = _norm(path)
        if key not in self._file_lines:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    self._file_lines[key] = fh.read().splitlines()
            except OSError:
                self._file_lines[key] = []
        return self._file_lines[key]

    def snippet(self, path: str, line: int, context: int = 0) -> str:
        lines = self.source_lines(path)
        if not lines or line <= 0 or line > len(lines):
            return ""
        return "\n".join(lines[max(0, line - 1 - context) : min(len(lines), line + context)])

    def file_node(self, path: str, force: bool = False) -> str:
        if not path:
            return ""
        if not force and not self.is_relevant_file(path):
            # system/SDK headers are summarised per file instead of becoming
            # hundreds of nodes (a single libc++ include pulls in ~600 files)
            return ""
        self.touched_files.add(_norm(path))
        rel = self.rel_path(path)
        node_id = f"FILE:{rel}"
        external = not self.is_project_file(path)
        attrs = {
            "file": rel,
            "path": path.replace("\\", "/"),
            "kind": "header" if rel.lower().endswith((".h", ".hpp")) else "source",
            "external": external,
        }
        if not external and os.path.exists(path):
            lines = self.source_lines(path)
            attrs["lines"] = len(lines)
            attrs["bytes"] = os.path.getsize(path)
        return self.graph.node(node_id, "FILE", rel, **attrs)

    def declared_node(self, cursor: CXCursor, kind: str | None = None) -> str:
        key = cursor_key(cursor)
        cached = self._key_to_id.get(key)
        if cached:
            return cached
        tu = self._tu
        etype = kind or DECL_KIND_TO_ENTITY.get(cursor.kind, "EXTERNAL")
        name = tu.spelling(cursor) or tu.display_name(cursor)
        loc = tu.expansion_location(cursor)
        spell = tu.spelling_location(cursor)
        usr = tu.usr(cursor)
        node_id = f"{etype}:{usr}" if usr else f"{etype}:{name}@{self.rel_path(loc.file)}:{loc.line}"
        attrs = {
            "file": self.rel_path(loc.file),
            "path": loc.file.replace("\\", "/"),
            "line": loc.line,
            "column": loc.column,
            "external": not self.is_project_file(loc.file),
            "usr": usr,
            "is_definition": bool(tu.is_definition(cursor)),
        }
        if spell.file and (_norm(spell.file) != _norm(loc.file) or spell.line != loc.line):
            attrs["macro_spelling_site"] = f"{self.rel_path(spell.file)}:{spell.line}:{spell.column}"
            attrs["is_macro_generated"] = True
        node_id = self.graph.node(node_id, etype, name or f"<anonymous {etype.lower()}>", **attrs)
        self._key_to_id[key] = node_id
        return node_id

    def _owner(self, path: list[CXCursor]) -> str | None:
        for cursor in reversed(path):
            node_id = self._key_to_id.get(cursor_key(cursor))
            if node_id:
                return node_id
        return None

    def _enclosing_function(self, path: list[CXCursor]) -> str | None:
        for cursor in reversed(path):
            if cursor.kind in FUNCTION_LIKE_KINDS:
                return self._key_to_id.get(cursor_key(cursor))
        return None

    def _cursor_offset(self, cursor: CXCursor) -> int:
        return _offset_of(self._tu.lib, self._tu.lib.clang_getCursorLocation(cursor))

    def _extent_end(self, extent) -> tuple[int, int]:
        pos = self._tu._loc(self._tu.lib.clang_getRangeEnd(extent), "expansion")
        return pos.line, pos.column

    def _range_start_offset(self, cursor: CXCursor) -> int:
        extent = self._tu.lib.clang_getCursorExtent(cursor)
        return _offset_of(self._tu.lib, self._tu.lib.clang_getRangeStart(extent))

    def _binop_operator(self, cursor: CXCursor) -> tuple[str, int]:
        """Return (operator token, operator offset) for a binary/unary expression.

        Fast path: tokenize the expression.  That fails when the left-hand side
        comes from a macro expansion -- ``clang_tokenize`` then walks the
        *spelling* range (the macro body) and never sees the ``=``.  Fallback:
        split the expression at its direct children and read the operator text
        straight out of the source file, which is always where the user wrote
        the assignment.
        """
        key = cursor_key(cursor)
        cached = self._binop_cache.get(key)
        if cached is not None:
            return cached
        depth = 0
        found = ("", -1)
        for token in self._tu.tokens_of(cursor):
            text = token.spelling
            if text in "([":
                depth += 1
            elif text in ")]":
                depth -= 1
            elif text == "=" or (depth == 0 and text in OPERATOR_TOKENS):
                found = (text, token.offset)
                break
        self._binop_cache[key] = found
        if not found[0]:
            fallback = self._operator_between_children(cursor)
            if fallback[0]:
                self._binop_cache[key] = fallback
                return fallback
        return found

    def _operator_between_children(self, cursor: CXCursor) -> tuple[str, int]:
        children: list[CXCursor] = []

        def collector(child, _parent, _data):
            children.append(child)
            return capi.CXChildVisit_Continue

        callback = capi._CURSOR_VISITOR(collector)
        self._tu._captured.append(callback)
        try:
            self._tu.lib.clang_visitChildren(cursor, callback, None)
        finally:
            self._tu._captured.pop()
        if len(children) < 2:
            return ("", -1)

        lhs_end = self._range_end_offset(children[0])
        rhs_start = self._cursor_offset(children[-1])
        if lhs_end < 0 or rhs_start <= lhs_end:
            return ("", -1)

        lhs_loc = self._tu._loc(self._tu.lib.clang_getRangeEnd(
            self._tu.lib.clang_getCursorExtent(children[0])), "expansion")
        rhs_loc = self._tu.expansion_location(children[-1])
        if _norm(lhs_loc.file) != _norm(rhs_loc.file):
            return ("", -1)
        raw = self._file_bytes(lhs_loc.file)
        if not raw:
            return ("", -1)
        between = raw[lhs_end:rhs_start].decode("utf-8", "replace").strip()
        if between in ASSIGN_OPERATORS or between in OPERATOR_TOKENS:
            return (between, lhs_end)
        return ("", -1)

    def _file_bytes(self, path: str) -> bytes:
        key = _norm(path)
        cached = self._file_bytes_cache.get(key)
        if cached is None:
            try:
                with open(path, "rb") as fh:
                    cached = fh.read()
            except OSError:
                cached = b""
            self._file_bytes_cache[key] = cached
        return cached

    def _range_end_offset(self, cursor: CXCursor) -> int:
        extent = self._tu.lib.clang_getCursorExtent(cursor)
        return _offset_of(self._tu.lib, self._tu.lib.clang_getRangeEnd(extent))

    def _unop_token(self, cursor: CXCursor) -> str:
        key = cursor_key(cursor)
        cached = self._unop_cache.get(key)
        if cached is None:
            tokens = self._tu.tokens_of(cursor)
            # the operator may be a prefix (++x) or a postfix (x++) token
            prefix = tokens[0].spelling if tokens else ""
            suffix = tokens[-1].spelling if tokens else ""
            cached = prefix if prefix in ("++", "--", "&", "*", "!") else suffix
            self._unop_cache[key] = cached
        return cached

    def _edge_once(self, head: str, tail: str, rel: str, site: str) -> bool:
        key = (head, rel, tail, site)
        if key in self._seen_edges:
            return False
        self._seen_edges.add(key)
        return True

    # ==================================================================
    # type resolution
    # ==================================================================
    def _type_targets(self, ctype, depth: int = 0) -> list[tuple[CXCursor, str]]:
        """Named declarations referenced by a clang type, outermost first."""
        out: list[tuple[CXCursor, str]] = []
        if depth > 4:
            return out
        lib = self._tu.lib
        kind = ctype.kind
        if kind == capi.CXType_Pointer:
            return self._type_targets(lib.clang_getPointeeType(ctype), depth + 1)
        if kind in (capi.CXType_ConstantArray, capi.CXType_IncompleteArray):
            return self._type_targets(lib.clang_getArrayElementType(ctype), depth + 1)
        decl = lib.clang_getTypeDeclaration(ctype)
        if decl.kind not in TYPE_DECL_KINDS:
            return out
        out.append((decl, "direct"))
        if decl.kind == capi.CXCursor_TypedefDecl:
            canonical = lib.clang_getCanonicalType(ctype)
            underlying = lib.clang_getTypeDeclaration(canonical)
            if underlying.kind in TYPE_DECL_KINDS and cursor_key(underlying) != cursor_key(decl):
                out.append((underlying, "via-typedef"))
        return out

    # ==================================================================
    # driver
    # ==================================================================
    def run(self, clang, sources: list[str], extra_args: list[str], finalize: bool = True) -> dict:
        for source in sources:
            self._parse_one(clang, source, extra_args)
        if finalize:
            self.finalize()
        return {
            "files_parsed": len(sources),
            "macro_definitions": len(self.macro_defs),
            "expansion_sites": len(self.expansions),
            "diagnostics": len(self.diagnostics),
        }

    def finalize(self) -> None:
        """Cross-link macros once all translation units have been merged."""
        self.expansions = finalize_macros(
            self.graph,
            self.macro_defs,
            self.macro_def_locations,
            self.expansions,
            self.config,
            self.pointer_assignments,
            self.log,
        )

    def _args_for(self, source: str, extra_args: list[str]) -> list[str]:
        if self.args_builder is not None:
            args = self.args_builder.for_file(source)
            return args + list(extra_args)
        args: list[str] = []
        for directory in self.config.shim_include_dirs:
            args += ["-I", directory]
        for directory in self.config.include_dirs:
            args += ["-I", directory]
        args += ["-x", "c-header" if source.lower().endswith((".h", ".hpp")) else "c"]
        args += ["-std=" + self.config.std]
        if self.config.target:
            args.append("--target=" + self.config.target)
        for define in self.config.defines:
            args.append("-D" + define)
        args += ["-ferror-limit=0", "-Wno-everything"]
        args += list(extra_args)
        return args

    def _parse_one(self, clang, source: str, extra_args: list[str]) -> None:
        tu = load_translation_unit(clang, source, self._args_for(source, extra_args))
        self._tu = tu
        self._key_to_id = {}
        self._binop_cache = {}
        self._unop_cache = {}
        self._lhs_of = {}
        self._file_bytes_cache = {}
        self.file_node(source)
        nodes_before = len(self.graph.nodes)
        try:
            tu.walk(self._visit)
            self._collect_includes(tu)
            diags = tu.diagnostics()
        except Exception as exc:  # keep analysing the remaining files
            self.log(f"  ! {self.rel_path(source)}: {type(exc).__name__}: {exc}")
            diags = [{"severity": capi.CXDiagnostic_Error, "severity_name": "error",
                      "text": f"{type(exc).__name__}: {exc}", "location": {}}]
        finally:
            self._tu = None
        for diag in diags:
            diag["source"] = self.rel_path(source)
        self.diagnostics.extend(diags)
        errors = sum(1 for d in diags if d["severity"] >= capi.CXDiagnostic_Error)
        created = len(self.graph.nodes) - nodes_before
        self.files_parsed.append(
            {
                "file": self.rel_path(source),
                "diagnostics": len(diags),
                "errors": errors,
                "entities": created,
                "quality": _quality(errors, created),
            }
        )
        tu.dispose()

    # ==================================================================
    # AST visitor
    # ==================================================================
    def _visit(self, cursor: CXCursor, parent: CXCursor, path: list[CXCursor]) -> None:
        kind = cursor.kind
        if kind == capi.CXCursor_MacroDefinition:
            self._on_macro_definition(cursor)
        elif kind == capi.CXCursor_MacroExpansion:
            self._on_macro_expansion(cursor, path)
        elif kind in DECL_KIND_TO_ENTITY:
            self._on_declaration(cursor, path)
        elif kind == capi.CXCursor_DeclRefExpr:
            self._on_decl_ref(cursor, path)
        elif kind == capi.CXCursor_MemberRefExpr:
            self._on_member_ref(cursor, path)
        elif kind == capi.CXCursor_CallExpr:
            self._on_call(cursor, path)
        elif kind == capi.CXCursor_CXXBaseSpecifier:
            self._on_base_specifier(cursor, path)

    # ------------------------------------------------------------------
    def _on_base_specifier(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        """``class Derived : public Base`` -> INHERITS edge."""
        owner = None
        for ancestor in reversed(path):
            if ancestor.kind in DECL_KIND_TO_ENTITY and ancestor.kind in TYPE_LIKE_KINDS:
                owner = self._key_to_id.get(cursor_key(ancestor))
                break
        if not owner:
            return
        ctype = self._tu.cursor_type(cursor)
        loc = self._tu.expansion_location(cursor)
        for target, _via in self._type_targets(ctype):
            if not self.is_relevant_file(self._tu.expansion_location(target).file):
                continue
            self.graph.edge(owner, self.declared_node(target), "INHERITS",
                            line=loc.line, file=self.rel_path(loc.file))

    def _link_method_to_class(self, cursor: CXCursor, node_id: str, path: list[CXCursor]) -> None:
        """Attach an out-of-line method definition to its class."""
        for ancestor in reversed(path):
            if ancestor.kind in TYPE_LIKE_KINDS:
                owner = self._key_to_id.get(cursor_key(ancestor))
                if owner:
                    self.graph.edge(owner, node_id, "HAS_METHOD")
                return
        semantic = self._tu.lib.clang_getCursorSemanticParent(cursor)
        if semantic.kind in TYPE_LIKE_KINDS:
            parent = self.declared_node(
                semantic, DECL_KIND_TO_ENTITY.get(semantic.kind, "CLASS")
            )
            if parent:
                self.graph.edge(parent, node_id, "HAS_METHOD")

    # ------------------------------------------------------------------
    def _on_declaration(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        kind = cursor.kind
        loc = self._tu.expansion_location(cursor)
        if not self.is_relevant_file(loc.file):
            # a declaration from a system/SDK header: only materialise it when
            # project code refers to it (handled by declared_node)
            return
        owner = self._owner(path)

        if kind in TYPE_LIKE_KINDS:
            etype = DECL_KIND_TO_ENTITY[kind]
            name = self._tu.spelling(cursor)
            if not name and owner:
                name = self.graph.nodes.get(owner, {}).get("name", "")
            node_id = self.declared_node(cursor, etype)
            node = self.graph.nodes[node_id]
            node["type"] = etype
            if name:
                node["name"] = name
                keyword = {"ENUM": "enum", "CLASS": "class", "UNION": "union"}.get(etype, "struct")
                node["type_spelling"] = f"{keyword} {name}"
            if kind == capi.CXCursor_EnumDecl:
                node["underlying_type"] = self._tu.type_spelling(
                    self._tu.lib.clang_getEnumDeclIntegerType(cursor)
                )
            parent_id = owner or self.file_node(loc.file)
            self.graph.edge(parent_id, node_id, "CONTAINS", line=loc.line)
            return

        if kind in (capi.CXCursor_Namespace, capi.CXCursor_NamespaceAlias):
            node_id = self.declared_node(cursor, "NAMESPACE")
            node = self.graph.nodes[node_id]
            node["type"] = "NAMESPACE"
            self.graph.edge(owner or self.file_node(loc.file), node_id, "CONTAINS", line=loc.line)
            self.graph.edge(self.file_node(loc.file), node_id, "CONTAINS", line=loc.line)
            return

        if kind == capi.CXCursor_TypedefDecl:
            node_id = self.declared_node(cursor, "TYPEDEF")
            node = self.graph.nodes[node_id]
            underlying = self._tu.lib.clang_getTypedefDeclUnderlyingType(cursor)
            node["type_spelling"] = self._tu.type_spelling(underlying)
            parent_id = owner or self.file_node(loc.file)
            self.graph.edge(parent_id, node_id, "CONTAINS", line=loc.line)
            for target, via in self._type_targets(underlying):
                self.graph.edge(node_id, self.declared_node(target), "TYPEDEF_OF", via=via, line=loc.line)
            return

        if kind == capi.CXCursor_FieldDecl:
            node_id = self.declared_node(cursor, "FIELD")
            node = self.graph.nodes[node_id]
            ctype = self._tu.cursor_type(cursor)
            node["type_spelling"] = self._tu.type_spelling(ctype)
            node["is_pointer"] = _is_pointer_type(ctype)
            if owner:
                self.graph.edge(owner, node_id, "HAS_MEMBER", line=loc.line)
            for target, via in self._type_targets(ctype):
                self.graph.edge(node_id, self.declared_node(target), "TYPE_OF", via=via, line=loc.line)
            return

        if kind == capi.CXCursor_EnumConstantDecl:
            node_id = self.declared_node(cursor, "ENUM_CONST")
            self.graph.nodes[node_id]["value"] = int(self._tu.lib.clang_getEnumConstantDeclValue(cursor))
            if owner:
                self.graph.edge(owner, node_id, "CONTAINS", line=loc.line)
            return

        if kind in FUNCTION_LIKE_KINDS:
            node_id = self.declared_node(cursor, "FUNCTION")
            node = self.graph.nodes[node_id]
            node["type"] = DECL_KIND_TO_ENTITY[kind]
            ftype = self._tu.cursor_type(cursor)
            node["signature"] = self._signature(cursor)
            node["return_type"] = self._tu.type_spelling(self._tu.lib.clang_getResultType(ftype))
            node["storage_class"] = self._tu.storage_class(cursor)
            node["is_static"] = self._tu.storage_class(cursor) == "static"
            is_definition = bool(self._tu.is_definition(cursor))
            if is_definition:
                self.functions_defined.add(node_id)
                node["is_definition"] = True
                node["file"] = self.rel_path(loc.file)
                node["path"] = loc.file.replace("\\", "/")
                node["line"] = loc.line
                node["column"] = loc.column
                node["end_line"] = self._extent_end(self._tu.lib.clang_getCursorExtent(cursor))[0]
                node["metrics"] = self._function_metrics(cursor)
            if not self.is_project_file(loc.file):
                node["external"] = True
            nargs = self._tu.lib.clang_Cursor_getNumArguments(cursor)
            for i in range(max(nargs, 0)):
                param = self._tu.lib.clang_Cursor_getArgument(cursor, i)
                param_id = self.declared_node(param, "PARAMETER")
                self.graph.edge(node_id, param_id, "HAS_PARAMETER", line=loc.line)
            self.graph.edge(owner or self.file_node(loc.file), node_id, "CONTAINS", line=loc.line)
            if node["type"] == "METHOD":
                self._link_method_to_class(cursor, node_id, path)
            ret = self._tu.lib.clang_getResultType(ftype)
            for target, via in self._type_targets(ret):
                self.graph.edge(node_id, self.declared_node(target), "RETURNS", via=via, line=loc.line)
            return

        if kind in (capi.CXCursor_VarDecl, capi.CXCursor_ParmDecl):
            is_param = kind == capi.CXCursor_ParmDecl
            etype = "PARAMETER" if is_param else ("LOCAL" if owner else "VARIABLE")
            node_id = self.declared_node(cursor, etype)
            node = self.graph.nodes[node_id]
            node["type"] = etype
            ctype = self._tu.cursor_type(cursor)
            node["type_spelling"] = self._tu.type_spelling(ctype)
            node["is_pointer"] = _is_pointer_type(ctype)
            node["storage_class"] = self._tu.storage_class(cursor)
            if is_param:
                node["category"] = "parameter"
            elif owner:
                node["category"] = "local"
                node["scope"] = self.graph.nodes.get(owner, {}).get("name", "")
            else:
                node["category"] = "global"
                node["is_definition"] = bool(self._tu.is_definition(cursor))
            if is_param and owner:
                self.graph.edge(owner, node_id, "HAS_PARAMETER", line=loc.line)
            elif owner:
                self.graph.edge(owner, node_id, "HAS_VARIABLE", line=loc.line)
            elif loc.file:
                self.graph.edge(self.file_node(loc.file), node_id, "CONTAINS", line=loc.line)
            if not self.is_project_file(loc.file):
                node["external"] = True
            for target, via in self._type_targets(ctype):
                self.graph.edge(node_id, self.declared_node(target), "TYPE_OF", via=via, line=loc.line)
            return

    # ------------------------------------------------------------------
    def _on_macro_definition(self, cursor: CXCursor) -> None:
        name = self._tu.spelling(cursor)
        if not name:
            return
        loc = self._tu.expansion_location(cursor)
        is_function_like: bool | None = None
        try:
            if self._tu.lib.clang_Cursor_isMacroFunctionLike(cursor):
                is_function_like = True
        except Exception:  # pragma: no cover
            is_function_like = None
        # libclang reports the extent of an object-like macro as the name only,
        # so the body is re-read from the source text.
        _, is_function_like, params, body, raw = parse_define(
            self.source_lines(loc.file), loc.line, loc.column, is_function_like
        )
        if not body:
            tokens = [
                (t.spelling, t.kind == capi.CXToken_Identifier)
                for t in self._tu.tokens_of(cursor)
            ]
            _, body = split_definition(tokens)
            raw = " ".join(t[0] for t in tokens)
        end_line, end_col = self._extent_end(self._tu.lib.clang_getCursorExtent(cursor))
        definition = MacroDef(
            name=name,
            file=self.rel_path(loc.file),
            line=loc.line,
            column=loc.column,
            end_line=end_line,
            end_column=end_col,
            is_function_like=bool(is_function_like),
            params=params,
            tokens=body,
            raw_text=raw,
            doc=self._leading_comment(loc.file, loc.line),
        )
        self.macro_defs.setdefault(name, definition)
        self.macro_def_locations[name].append(definition)
        if not self.is_relevant_file(loc.file):
            # keep the definition (expansion still needs the body) but do not
            # turn every system header macro into a graph node; it appears only
            # when project code actually uses it
            return
        node_id = f"MACRO:{name}"
        self.graph.node(
            node_id,
            "MACRO",
            name,
            file=self.rel_path(loc.file),
            path=loc.file.replace("\\", "/"),
            line=loc.line,
            column=loc.column,
            end_line=end_line,
            body=definition.body_text,
            params=params,
            is_function_like=bool(is_function_like),
            doc=definition.doc,
            external=not self.is_project_file(loc.file),
            locations=[f"{self.rel_path(loc.file)}:{loc.line}"],
        )
        self.graph.edge(self.file_node(loc.file), node_id, "CONTAINS", line=loc.line)

    def _on_macro_expansion(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        name = self._tu.spelling(cursor)
        if not name:
            ref = self._tu.lib.clang_getCursorReferenced(cursor)
            name = capi.cxstring_to_str(self._tu.lib.clang_getCursorSpelling(ref))
            if not name:
                return
        loc = self._tu.expansion_location(cursor)
        if not self.is_relevant_file(loc.file):
            # expansion happening inside a library header (libc++, musl, ...):
            # not part of the project's own code
            return
        tokens = self._tu.tokens_of(cursor)
        invocation = "".join(t.spelling for t in tokens) or name
        args: list[str] = []
        if len(tokens) > 1 and tokens[1].spelling == "(":
            depth = 0
            current: list[str] = []
            for token in tokens[1:]:
                text = token.spelling
                if text == "(":
                    depth += 1
                    if depth == 1:
                        continue
                if text == ")":
                    depth -= 1
                    if depth == 0:
                        args.append("".join(current))
                        break
                if text == "," and depth == 1:
                    args.append("".join(current))
                    current = []
                    continue
                current.append(text)
            args = [a for a in args if a]
        end_line, end_col = self._extent_end(self._tu.lib.clang_getCursorExtent(cursor))
        owner = self._enclosing_function(path) or self._owner(path) or ""
        expansion = Expansion(
            file=self.rel_path(loc.file),
            line=loc.line,
            column=loc.column,
            end_line=end_line,
            end_column=end_col,
            invocation=invocation,
            macro=name,
            expanded="",
            arguments=args,
            owner=owner,
        )
        self.expansions.append(expansion)
        self._expansion_index[(expansion.file, expansion.line)].append(expansion)

    # ------------------------------------------------------------------
    def _on_decl_ref(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        ref = self._tu.lib.clang_getCursorReferenced(cursor)
        if not ref or not ref.kind:
            return
        rkind = ref.kind
        if rkind in FUNCTION_LIKE_KINDS:
            self._record_function_assignment(cursor, ref, path)
            return
        if rkind not in (
            capi.CXCursor_VarDecl,
            capi.CXCursor_ParmDecl,
            capi.CXCursor_FieldDecl,
            capi.CXCursor_EnumConstantDecl,
        ):
            return
        owner = self._enclosing_function(path)
        if not owner:
            return
        target = self.declared_node(ref)
        mode = self._access_mode(cursor, path)
        loc = self._tu.expansion_location(cursor)
        site = f"{self.rel_path(loc.file)}:{loc.line}"
        via_macro = self._via_macro(cursor, loc)
        extra = {"via_macro": via_macro} if via_macro else {}
        self._lhs_priority = 1
        if mode == "write" and self._remember_lhs(path, target):
            pass
        rel = "WRITES" if mode == "write" else "READS"
        if self._edge_once(owner, target, rel, site):
            self.graph.edge(
                owner, target, rel, line=loc.line, file=self.rel_path(loc.file), sites=[site], **extra
            )
        if mode == "readwrite":
            if self._edge_once(owner, target, "READS", site + "#rw"):
                self.graph.edge(
                    owner, target, "READS", line=loc.line, file=self.rel_path(loc.file), sites=[site], **extra
                )
            if self._edge_once(owner, target, "WRITES", site + "#rw"):
                self.graph.edge(
                    owner, target, "WRITES", line=loc.line, file=self.rel_path(loc.file), sites=[site], **extra
                )

    def _on_member_ref(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        ref = self._tu.lib.clang_getCursorReferenced(cursor)
        if not ref or ref.kind != capi.CXCursor_FieldDecl:
            return
        owner = self._enclosing_function(path)
        if not owner:
            return
        target = self.declared_node(ref)
        mode = self._access_mode(cursor, path)
        loc = self._tu.expansion_location(cursor)
        site = f"{self.rel_path(loc.file)}:{loc.line}"
        via_macro = self._via_macro(cursor, loc)
        extra = {"via_macro": via_macro} if via_macro else {}
        rel = "WRITES" if mode in ("write", "readwrite") else "READS"
        if self._edge_once(owner, target, rel, site):
            self.graph.edge(
                owner, target, rel, line=loc.line, file=self.rel_path(loc.file), sites=[site], **extra
            )
        if mode == "write":
            self._lhs_priority = 2  # a member expression is more specific than its base
            self._remember_lhs(path, target)

    def _via_macro(self, cursor: CXCursor, loc) -> str:
        """Name of the macro that produced this reference, if any.

        ``s_ctx.iq_ref`` is written as such in the source, but clang resolves it
        to ``g_foc_ctx[...].iq_ref``: the spelling location points into the
        ``#define s_ctx`` body, the expansion location at the use site.
        """
        spell = self._tu.spelling_location(cursor)
        if spell.file and not (_norm(spell.file) == _norm(loc.file) and spell.line == loc.line):
            name = self._macro_at(spell.file, spell.line)
            if name:
                return name
        # fall back to the macro expansion range covering this position
        for expansion in self._expansion_index.get((self.rel_path(loc.file), loc.line), []):
            end_col = expansion.end_column if expansion.end_line == loc.line else 10 ** 6
            if expansion.column <= loc.column <= max(end_col, expansion.column):
                return expansion.macro
        return ""

    def _on_call(self, cursor: CXCursor, path: list[CXCursor]) -> None:
        owner = self._enclosing_function(path)
        if not owner:
            return
        ref = self._tu.lib.clang_getCursorReferenced(cursor)
        loc = self._tu.expansion_location(cursor)
        spell = self._tu.spelling_location(cursor)
        via_macro = _norm(spell.file) != _norm(loc.file) or spell.line != loc.line
        site = f"{self.rel_path(loc.file)}:{loc.line}"
        if ref and ref.kind in FUNCTION_LIKE_KINDS:
            target = self.declared_node(ref)
            if self._edge_once(owner, target, "CALLS", site):
                self.graph.edge(
                    owner, target, "CALLS",
                    line=loc.line, file=self.rel_path(loc.file), sites=[site], via_macro=via_macro,
                )
            return
        if ref and ref.kind == capi.CXCursor_VarDecl:
            target = self.declared_node(ref)
            if self._edge_once(owner, target, "CALLS_PTR", site):
                self.graph.edge(
                    owner, target, "CALLS_PTR", line=loc.line, file=self.rel_path(loc.file), sites=[site]
                )
            return
        if ref and ref.kind == capi.CXCursor_FieldDecl:
            # `dev->init()`: an indirect call through a function-pointer field
            target = self.declared_node(ref)
            if self._edge_once(owner, target, "CALLS_PTR", site):
                self.graph.edge(
                    owner, target, "CALLS_PTR", line=loc.line, file=self.rel_path(loc.file), sites=[site]
                )
            return
        if via_macro:
            macro_name = self._macro_at(spell.file, spell.line)
            if macro_name:
                macro_node = f"MACRO:{macro_name}"
                if macro_node in self.graph.nodes:
                    self.graph.edge(
                        owner, macro_node, "CALLS_MACRO",
                        line=loc.line, file=self.rel_path(loc.file), sites=[site],
                    )

    def _record_function_assignment(
        self, site_cursor: CXCursor, fn_cursor: CXCursor, path: list[CXCursor]
    ) -> None:
        """``fn_ptr = handler`` / ``{ .cb = handler }`` bindings."""
        # a bare function designator as the callee of a call is handled by _on_call
        site_offset = self._cursor_offset(site_cursor)
        for ancestor in reversed(path):
            if ancestor.kind == capi.CXCursor_CallExpr:
                if site_offset == self._range_start_offset(ancestor):
                    return
                break
        target = self.declared_node(fn_cursor)
        holder = None
        for cursor in reversed(path):
            if cursor.kind == capi.CXCursor_BinaryOperator:
                entry = self._lhs_of.get(cursor_key(cursor))
                holder = entry[1] if entry else None
                break
            if cursor.kind == capi.CXCursor_InitListExpr:
                holder = self._init_list_holder(path)
                break
        if not holder:
            owner = self._enclosing_function(path)
            if owner:
                loc = self._tu.expansion_location(site_cursor)
                site = f"{self.rel_path(loc.file)}:{loc.line}"
                if self._edge_once(owner, target, "READS", "fnptr" + site):
                    self.graph.edge(
                        owner, target, "READS", line=loc.line, file=self.rel_path(loc.file), sites=[site]
                    )
            return
        loc = self._tu.expansion_location(site_cursor)
        site = f"{self.rel_path(loc.file)}:{loc.line}"
        if self._edge_once(holder, target, "ASSIGNED_TO", site):
            self.graph.edge(
                holder, target, "ASSIGNED_TO", line=loc.line, file=self.rel_path(loc.file), sites=[site]
            )
        self.pointer_assignments[holder].add(target)

    def _remember_lhs(self, path: list[CXCursor], node_id: str) -> bool:
        """Remember what an assignment writes to.

        ``dev->init = handler`` produces two candidate left-hand sides: the
        member expression (the field, which is what we want) and the base
        variable ``dev``; the caller passes a priority so the field wins.
        """
        priority = self._lhs_priority
        for cursor in reversed(path):
            if cursor.kind in (capi.CXCursor_BinaryOperator, capi.CXCursor_CompoundAssignOperator):
                key = cursor_key(cursor)
                previous = self._lhs_of.get(key)
                if previous is None or priority >= previous[0]:
                    self._lhs_of[key] = (priority, node_id)
                return True
            if cursor.kind in (capi.CXCursor_CallExpr, capi.CXCursor_ReturnStmt):
                return False
        return False

    def _init_list_holder(self, path: list[CXCursor]) -> str | None:
        for cursor in reversed(path):
            node_id = self._key_to_id.get(cursor_key(cursor))
            if node_id and self.graph.nodes.get(node_id, {}).get("type") in (
                "VARIABLE", "LOCAL", "PARAMETER", "FIELD",
            ):
                return node_id
        return None

    def _access_mode(self, cursor: CXCursor, path: list[CXCursor]) -> str:
        """Classify a reference as read / write / read-modify-write."""
        offset = self._cursor_offset(cursor)
        for index in range(len(path) - 1, -1, -1):
            ancestor = path[index]
            kind = ancestor.kind
            if kind in (capi.CXCursor_BinaryOperator, capi.CXCursor_CompoundAssignOperator):
                op, op_offset = self._binop_operator(ancestor)
                if op == "=":
                    return "write" if 0 <= op_offset and offset < op_offset else "read"
                if op in COMPOUND_ASSIGN:
                    return "readwrite"
                return "read"
            if kind == capi.CXCursor_UnaryOperator:
                token = self._unop_token(ancestor)
                if token in ("++", "--"):
                    return "readwrite"
                if token == "*":
                    # `*p = v` writes through the pointer
                    for outer in reversed(path[:index]):
                        if outer.kind in (
                            capi.CXCursor_BinaryOperator,
                            capi.CXCursor_CompoundAssignOperator,
                        ):
                            op, op_offset = self._binop_operator(outer)
                            if op == "=" and 0 <= op_offset and self._cursor_offset(ancestor) < op_offset:
                                return "write"
                            break
                return "read"
            if kind in (
                capi.CXCursor_CallExpr,
                capi.CXCursor_ReturnStmt,
                capi.CXCursor_IfStmt,
                capi.CXCursor_ForStmt,
                capi.CXCursor_WhileStmt,
                capi.CXCursor_ConditionalOperator,
            ):
                break
        return "read"

    def _macro_at(self, file: str, line: int) -> str | None:
        target_abs = _norm(file)
        target_rel = self.rel_path(file)
        for name, definitions in self.macro_def_locations.items():
            for definition in definitions:
                if definition.line > line:
                    continue
                if line > max(definition.end_line, definition.line):
                    continue
                if _norm(definition.file) == target_abs or definition.file == target_rel:
                    return name
        return None

    # ------------------------------------------------------------------
    def _collect_includes(self, tu: TranslationUnit) -> None:
        summary: dict[str, int] = {}
        for edge in tu.includes():
            src, dst = edge["from"], edge["included"]
            if not src or not dst:
                continue
            src_id = self.file_node(src, force=self.is_relevant_file(src))
            if self.is_relevant_file(dst):
                dst_id = self.file_node(dst, force=True)
                if src_id and dst_id and src_id != dst_id:
                    self.graph.edge(src_id, dst_id, "INCLUDES")
            elif src_id:
                summary[src] = summary.get(src, 0) + 1
        for source, count in summary.items():
            node_id = self.file_node(source, force=True)
            if node_id:
                self.graph.node(node_id, "FILE", self.rel_path(source),
                                system_headers=count)

    def _signature(self, cursor: CXCursor) -> str:
        """Reconstruct ``ret name(arg types)`` from clang's type information."""
        lib = self._tu.lib
        ftype = self._tu.cursor_type(cursor)
        ret = self._tu.type_spelling(lib.clang_getResultType(ftype))
        nargs = lib.clang_Cursor_getNumArguments(cursor)
        args: list[str] = []
        if nargs and nargs > 0:
            for i in range(nargs):
                args.append(self._tu.type_spelling(lib.clang_getCursorType(lib.clang_Cursor_getArgument(cursor, i))))
        elif nargs < 0:
            for i in range(max(lib.clang_getNumArgTypes(ftype), 0)):
                args.append(self._tu.type_spelling(lib.clang_getArgType(ftype, i)))
        if not args:
            spelling = self._tu.type_spelling(ftype)
            inside = spelling[spelling.find("(") + 1 : spelling.rfind(")")] if "(" in spelling else "void"
            args = [a.strip() for a in inside.split(",") if a.strip()] or ["void"]
        return f"{ret} {self._tu.spelling(cursor)}({', '.join(args)})"

    def _function_metrics(self, cursor: CXCursor) -> dict:
        counters = Counter()
        path: list[CXCursor] = [cursor]
        path_keys = [cursor_key(cursor)]

        def cb(child, parent, _data):
            pkey = cursor_key(parent)
            while len(path_keys) > 1 and path_keys[-1] != pkey:
                path_keys.pop()
                path.pop()
            if child.kind == capi.CXCursor_IfStmt:
                counters["branches"] += 1
            elif child.kind in (capi.CXCursor_ForStmt, capi.CXCursor_WhileStmt, capi.CXCursor_CaseStmt):
                counters["branches"] += 1
            elif child.kind == capi.CXCursor_CallExpr:
                counters["calls"] += 1
            path.append(child)
            path_keys.append(cursor_key(child))
            return capi.CXChildVisit_Recurse

        callback = capi._CURSOR_VISITOR(cb)
        self._tu._captured.append(callback)
        self._tu.lib.clang_visitChildren(cursor, callback, None)
        self._tu._captured.pop()
        start = self._tu.expansion_location(cursor).line
        end = self._extent_end(self._tu.lib.clang_getCursorExtent(cursor))[0]
        return {
            "lines": max(0, end - start + 1),
            "branches": counters["branches"],
            "cyclomatic": counters["branches"] + 1,
            "calls": counters["calls"],
        }

    def _leading_comment(self, file: str, line: int) -> str:
        lines = self.source_lines(file)
        out: list[str] = []
        i = line - 2
        while 0 <= i < len(lines):
            text = lines[i].strip()
            if not text:
                if out:
                    break
                i -= 1
                continue
            if text.startswith(("//", "/*", "*", "*/")):
                out.insert(0, text)
                i -= 1
                continue
            break
        return " ".join(t.strip("/* ").strip() for t in out)[:300]

    # ==================================================================
    # post processing
    # ==================================================================
    def _post_process(self) -> None:
        """Kept for backwards compatibility; see :func:`finalize_macros`."""
        self.finalize()

    def _macro_at_file(self, rel_file: str, line: int, absolute: str) -> str | None:
        return lookup_macro_at(macro_def_locations=self.macro_def_locations,
                               rel_file=rel_file, line=line, absolute=absolute)

    def _function_range_index(self) -> dict[str, list[tuple[int, int, str]]]:
        return function_range_index(self.graph)


def lookup_macro_at(macro_def_locations, rel_file: str, line: int, absolute: str) -> str | None:
    for name, definitions in macro_def_locations.items():
        for definition in definitions:
            if definition.line > line or line > max(definition.end_line, definition.line):
                continue
            if definition.file == rel_file or _norm(definition.file) == _norm(absolute):
                return name
    return None


def function_range_index(graph) -> dict[str, list[tuple[int, int, str]]]:
    """file -> sorted [(start_line, end_line, function_id)] for definitions."""
    index: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for node_id, node in graph.nodes.items():
        if node.get("type") != "FUNCTION" or not node.get("is_definition"):
            continue
        file = node.get("file")
        start = node.get("line")
        end = node.get("end_line")
        if not file or not start or not end:
            continue
        index[file].append((start, end, node_id))
    for entries in index.values():
        entries.sort()
    return index


def owner_at(index: dict[str, list[tuple[int, int, str]]], file: str, line: int) -> str:
    entries = index.get(file)
    if not entries:
        return ""
    best = ""
    best_start = -1
    for start, end, node_id in entries:
        if start > line:
            break
        if line <= end and start >= best_start:
            best, best_start = node_id, start
    return best


def finalize_macros(
    graph,
    macro_defs: dict,
    macro_def_locations: dict,
    expansions: list,
    config,
    pointer_assignments: dict | None = None,
    log=print,
) -> list:
    """Cross-link macros, expansion sites and generated declarations.

    Runs once after all translation units are merged; this is what makes the
    post-processing safe to use in the parallel pipeline as well.
    """
    expander = MacroExpander(macro_defs)
    owner_index = function_range_index(graph)

    for name, definition in list(macro_defs.items()):
        node_id = f"MACRO:{name}"
        if node_id not in graph.nodes:
            continue
        for referenced in expander.referenced_macros(definition):
            graph.edge(node_id, f"MACRO:{referenced}", "MACRO_DEPENDS_ON")
        body = definition.tokens
        if len(body) == 1 and body[0][1] and body[0][0] in macro_defs:
            graph.edge(node_id, f"MACRO:{body[0][0]}", "MACRO_ALIAS")
            graph.node(node_id, "MACRO", name, aliases=[body[0][0]])
        graph.node(node_id, "MACRO", name, expanded=expander.expand_definition(name))

    seen: set[tuple] = set()
    per_macro: Counter = Counter()
    ordered = sorted(expansions, key=lambda e: (e.file, e.line, e.column))
    for expansion in ordered:
        key = (expansion.file, expansion.line, expansion.column, expansion.macro)
        if key in seen:
            continue
        seen.add(key)
        definition = macro_defs.get(expansion.macro)
        if definition is not None:
            if definition.is_function_like and expansion.arguments:
                expansion.expanded = expander.expand_invocation(
                    expansion.macro, None, expansion.arguments
                )
            else:
                expansion.expanded = expander.expand_definition(expansion.macro)
        per_macro[expansion.macro] += 1
        macro_node = f"MACRO:{expansion.macro}"
        if macro_node in graph.nodes:
            graph.nodes[macro_node]["expansion_count"] = per_macro[expansion.macro]
        elif macro_defs.get(expansion.macro) is not None:
            definition = macro_defs[expansion.macro]
            graph.node(
                macro_node, "MACRO", expansion.macro,
                file=definition.file, line=definition.line,
                body=definition.body_text, params=list(definition.params),
                is_function_like=definition.is_function_like,
                external=True, used_from_project=True,
            )
        if not expansion.owner:
            expansion.owner = owner_at(owner_index, expansion.file, expansion.line)
        if expansion.owner:
            graph.edge(
                expansion.owner,
                macro_node,
                "USES_MACRO",
                file=expansion.file,
                line=expansion.line,
                expanded=expansion.expanded,
                arguments=expansion.arguments,
                sites=[f"{expansion.file}:{expansion.line}"],
            )

    # declarations produced by the preprocessor -> MACRO GENERATES entity
    for node_id, node in list(graph.nodes.items()):
        spelling = node.get("macro_spelling_site")
        if not spelling:
            continue
        rel_file, line_text, _col = spelling.split(":")
        absolute = os.path.join(config.source_root, rel_file)
        macro_name = lookup_macro_at(macro_def_locations, rel_file, int(line_text), absolute)
        if not macro_name:
            continue
        macro_node = f"MACRO:{macro_name}"
        if macro_node in graph.nodes:
            graph.edge(macro_node, node_id, "GENERATES")
            graph.node(node_id, node.get("type", "EXTERNAL"), node.get("name", ""),
                       generated_by_macro=[macro_name])

    for var_id, targets in (pointer_assignments or {}).items():
        callers = [
            head for (head, rel, tail) in graph.edges
            if rel == "CALLS_PTR" and tail == var_id
        ]
        for caller in callers:
            for target in targets:
                graph.edge(caller, target, "CALLS", resolved_via="function-pointer")

    return sorted(seen and ([e for e in ordered if (e.file, e.line, e.column, e.macro) in seen]) or [], key=lambda e: (e.file, e.line, e.column))
