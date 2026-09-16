"""tree-sitter based extractor (the approach used by code_kg_with_tree-sitter).

It parses the *text* of a file, so it needs no compiler configuration and never
fails on missing headers.  In this toolchain it plays three roles:

* a cross-check that measures how much of a file clang actually saw,
* a fallback for files clang could not parse at all,
* an "as written" view, i.e. before any macro expansion happens.
"""

from __future__ import annotations

import os
from collections import Counter

try:  # pragma: no cover - import guard
    import tree_sitter_c
    from tree_sitter import Language, Parser

    _LANGUAGE = Language(tree_sitter_c.language())
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _LANGUAGE = None
    _AVAILABLE = False


DECL_NODES = {
    "function_definition": "FUNCTION",
    "struct_specifier": "STRUCT",
    "union_specifier": "UNION",
    "enum_specifier": "ENUM",
    "type_definition": "TYPEDEF",
}


class TreeSitterExtractor:
    def __init__(self, config, log=print) -> None:
        self.config = config
        self.log = log
        self.root = os.path.normcase(os.path.normpath(config.source_root))
        self.entities: list[dict] = []
        self.relations: list[dict] = []
        self.per_file: dict[str, dict] = {}
        self.counters: Counter = Counter()
        self.id_to_name: dict[str, str] = {}
        self.parser = Parser(_LANGUAGE) if _AVAILABLE else None

    @property
    def available(self) -> bool:
        return _AVAILABLE

    # ------------------------------------------------------------------
    def rel_path(self, path: str) -> str:
        norm = os.path.normcase(os.path.normpath(path))
        if norm.startswith(self.root):
            return os.path.relpath(path, self.config.source_root).replace("\\", "/")
        return path.replace("\\", "/")

    def analyze(self, paths: list[str]) -> None:
        if not self.available:
            self.log("  tree-sitter grammar not installed - skipping")
            return
        for path in paths:
            try:
                with open(path, "rb") as fh:
                    source = fh.read()
            except OSError:
                continue
            tree = self.parser.parse(source)
            rel = self.rel_path(path)
            before = len(self.entities)
            self._walk_file(tree.root_node, source, rel, file_node_id=f"FILE:{rel}")
            self.per_file[rel] = {
                "entities": len(self.entities) - before,
                "functions": self.counters["function"],
                "macros": self.counters["macro"],
                "structs": self.counters["struct"] + self.counters["union"],
            }

    # ------------------------------------------------------------------
    def _text(self, node, source: bytes) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def _add_entity(self, kind: str, name: str, file: str, line: int, **attrs) -> str:
        entity_id = f"TS:{kind}:{name}@{file}:{line}"
        record = {"id": entity_id, "type": kind, "name": name, "file": file, "line": line,
                  "engine": "tree-sitter"}
        record.update({k: v for k, v in attrs.items() if v not in (None, "", [])})
        self.entities.append(record)
        self.counters[kind.lower()] += 1
        self.id_to_name[entity_id] = name
        return entity_id

    def _walk_file(self, root, source: bytes, file: str, file_node_id: str) -> None:
        stack: list[tuple[object, str]] = [(root, file_node_id)]

        def visit(node, owner: str, scope_name: str) -> None:
            ntype = node.type

            if ntype in ("preproc_def", "preproc_function_def"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = self._text(name_node, source)
                params = []
                if ntype == "preproc_function_def":
                    params_node = node.child_by_field_name("parameters")
                    if params_node is not None:
                        params = [
                            self._text(c, source)
                            for c in params_node.children
                            if c.type == "identifier"
                        ]
                value_node = node.child_by_field_name("value")
                body = self._text(value_node, source) if value_node is not None else ""
                entity_id = self._add_entity(
                    "MACRO", name, file, node.start_point[0] + 1,
                    body=body, params=params, is_function_like=ntype == "preproc_function_def",
                )
                self.relations.append({"head": owner or file_node_id, "tail": entity_id,
                                       "type": "CONTAINS", "line": node.start_point[0] + 1})
                return

            if ntype == "preproc_include":
                path_node = node.child_by_field_name("path")
                if path_node is not None:
                    self.relations.append({
                        "head": file_node_id,
                        "tail": "FILE:" + self._text(path_node, source).strip('<>"'),
                        "type": "INCLUDES",
                        "line": node.start_point[0] + 1,
                    })
                return

            if ntype in ("struct_specifier", "union_specifier"):
                name_node = node.child_by_field_name("name")
                body = node.child_by_field_name("body")
                if body is None:
                    return
                name = self._text(name_node, source) if name_node is not None else "<anonymous>"
                kind = "STRUCT" if ntype == "struct_specifier" else "UNION"
                struct_id = self._add_entity(kind, name, file, node.start_point[0] + 1)
                self.relations.append({"head": owner or file_node_id, "tail": struct_id,
                                       "type": "CONTAINS", "line": node.start_point[0] + 1})
                for child in body.children:
                    if child.type != "field_declaration":
                        continue
                    for declarator in self._declarators(child):
                        field_name = self._text(declarator, source)
                        field_id = self._add_entity(
                            "FIELD", field_name, file, child.start_point[0] + 1,
                            type_spelling=self._text(child.child_by_field_name("type") or child, source)[:80]
                            if child.child_by_field_name("type") else "",
                        )
                        self.relations.append({"head": struct_id, "tail": field_id,
                                               "type": "HAS_MEMBER", "line": child.start_point[0] + 1})
                return

            if ntype == "enum_specifier":
                name_node = node.child_by_field_name("name")
                body = node.child_by_field_name("body")
                if body is None:
                    return
                name = self._text(name_node, source) if name_node is not None else "<anonymous>"
                enum_id = self._add_entity("ENUM", name, file, node.start_point[0] + 1)
                self.relations.append({"head": owner or file_node_id, "tail": enum_id,
                                       "type": "CONTAINS", "line": node.start_point[0] + 1})
                for child in body.children:
                    if child.type == "enumerator":
                        cname = child.child_by_field_name("name")
                        if cname is not None:
                            cid = self._add_entity("ENUM_CONST", self._text(cname, source), file,
                                                   child.start_point[0] + 1)
                            self.relations.append({"head": enum_id, "tail": cid, "type": "CONTAINS",
                                                   "line": child.start_point[0] + 1})
                return

            if ntype == "type_definition":
                for declarator in self._declarators(node):
                    name = self._text(declarator, source)
                    typename = name
                    # strip pointer/array noise from typedef declarators
                    typename = typename.replace("*", "").strip()
                    if not typename:
                        continue
                    indent = node.child_by_field_name("declarator")
                    if indent is not None and indent.type == "type_identifier":
                        pass
                    entity_id = self._add_entity("TYPEDEF", name, file, node.start_point[0] + 1)
                    self.relations.append({"head": owner or file_node_id, "tail": entity_id,
                                           "type": "CONTAINS", "line": node.start_point[0] + 1})
                return

            if ntype == "function_definition":
                declarator = node.child_by_field_name("declarator")
                name_node = self._find_identifier(declarator)
                if name_node is None:
                    return
                name = self._text(name_node, source)
                func_id = self._add_entity(
                    "FUNCTION", name, file, node.start_point[0] + 1,
                    signature=self._text(node, source).split("{")[0].strip()[:200],
                    is_definition=True,
                )
                self.relations.append({"head": owner or file_node_id, "tail": func_id,
                                       "type": "CONTAINS", "line": node.start_point[0] + 1})
                params = self._parameters(declarator, source)
                for pname, ptype in params:
                    pid = self._add_entity("PARAMETER", pname, file, node.start_point[0] + 1,
                                           type_spelling=ptype)
                    self.relations.append({"head": func_id, "tail": pid, "type": "HAS_PARAMETER",
                                           "line": node.start_point[0] + 1})
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, func_id, name)
                return

            if ntype == "declaration":
                if self._is_prototype(node):
                    for declarator in self._declarators(node):
                        fname = self._text(declarator, source).strip()
                        if not fname:
                            continue
                        func_id = self._add_entity(
                            "FUNCTION", fname, file, node.start_point[0] + 1,
                            signature=self._text(node, source).split("{")[0].strip()[:200],
                            is_definition=False,
                        )
                        self.relations.append({"head": owner or file_node_id, "tail": func_id,
                                               "type": "CONTAINS", "line": node.start_point[0] + 1})
                    return
                for declarator in self._declarators(node):
                    dname = self._text(declarator, source).strip()
                    if not dname or not dname.isidentifier():
                        continue
                    type_node = node.child_by_field_name("type")
                    type_spelling = self._text(type_node, source) if type_node is not None else ""
                    kind = "LOCAL" if scope_name else "VARIABLE"
                    entity_id = self._add_entity(kind, dname, file, node.start_point[0] + 1,
                                                 type_spelling=type_spelling, scope=scope_name)
                    if scope_name:
                        self.relations.append({"head": owner, "tail": entity_id,
                                               "type": "HAS_VARIABLE", "line": node.start_point[0] + 1})
                    else:
                        self.relations.append({"head": owner or file_node_id, "tail": entity_id,
                                               "type": "CONTAINS", "line": node.start_point[0] + 1})
                for child in node.children:
                    visit(child, owner, scope_name)
                return

            if ntype == "call_expression":
                callee = node.child_by_field_name("function")
                if callee is not None:
                    if callee.type == "identifier":
                        cname = self._text(callee, source)
                    elif callee.type == "field_expression":
                        field = callee.child_by_field_name("field")
                        cname = self._text(field, source) if field is not None else ""
                    else:
                        cname = ""
                    if cname:
                        self.relations.append({
                            "head": owner,
                            "tail": f"TS:NAME:FUNCTION:{cname}",
                            "type": "CALLS",
                            "line": node.start_point[0] + 1,
                            "callee_name": cname,
                        })

            for child in node.children:
                visit(child, owner, scope_name)

        visit(root, file_node_id, "")

    # ------------------------------------------------------------------
    @staticmethod
    def _is_prototype(node) -> bool:
        declarator = node.child_by_field_name("declarator")
        return declarator is not None and declarator.type == "function_declarator"

    @staticmethod
    def _find_identifier(node):
        if node is None:
            return None
        if node.type in ("identifier", "type_identifier", "field_identifier"):
            return node
        for child in node.children:
            found = TreeSitterExtractor._find_identifier(child)
            if found is not None:
                return found
        return None

    @staticmethod
    def _declarators(node) -> list:
        out = []
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            target = TreeSitterExtractor._find_identifier(declarator)
            if target is not None:
                out.append(target)
            return out
        for child in node.children:
            if child.type == "init_declarator":
                target = TreeSitterExtractor._find_identifier(child)
                if target is not None:
                    out.append(target)
            elif child.type == "declarator":
                target = TreeSitterExtractor._find_identifier(child)
                if target is not None:
                    out.append(target)
        return out

    @staticmethod
    def _parameters(declarator, source: bytes) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = []
        node = declarator
        while node is not None and node.type == "pointer_declarator":
            node = node.child_by_field_name("declarator")
        if node is None:
            return params
        plist = node.child_by_field_name("parameters")
        if plist is None:
            return params
        for child in plist.children:
            if child.type != "parameter_declaration":
                continue
            name_node = child.child_by_field_name("declarator")
            type_node = child.child_by_field_name("type")
            name = TreeSitterExtractor._text(name_node, source) if name_node is not None else ""
            ptype = TreeSitterExtractor._text(type_node, source) if type_node is not None else ""
            if name:
                params.append((name, (ptype + (" " if ptype else ""))[:60]))
        return params

    @staticmethod
    def _text(node, source: bytes) -> str:
        if node is None:
            return ""
        return source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    # ------------------------------------------------------------------
    def merge_into_graph(self, graph, coverage=None) -> dict:
        """Add tree-sitter entities that clang did not already describe.

        ``coverage`` decides whether a source line was actually compiled.  Lines
        that the preprocessor dropped (``#if 0`` and friends) exist only in the
        text, so tree-sitter is the only engine that can report them.
        """
        clang_keys: set[tuple] = set()
        for node in graph.nodes.values():
            if node.get("engine") == "tree-sitter":
                continue
            clang_keys.add((node["type"], node["name"]))
        by_name = {(node["type"], node["name"]): node["id"] for node in graph.nodes.values()}
        added = 0
        dead_code = 0
        unmatched = 0
        for entity in self.entities:
            if (entity["type"], entity["name"]) in clang_keys:
                continue
            node_id = entity["id"] if entity["id"] not in graph.nodes else entity["id"] + "#ts"
            attrs = {k: v for k, v in entity.items()
                     if k not in ("id", "type", "name", "file", "line", "engine")}
            attrs["engine"] = "tree-sitter"
            attrs["only_in_tree_sitter"] = True
            if entity["type"] in ("FUNCTION", "VARIABLE", "LOCAL", "MACRO", "PARAMETER", "FIELD"):
                compiled = True
                if coverage is not None:
                    compiled = coverage(entity["file"], entity["line"])
                if not compiled:
                    attrs["not_in_compiled_ast"] = True
                    attrs["dead_code"] = True
                    dead_code += 1
                else:
                    attrs["unmatched_by_clang"] = True
                    unmatched += 1
            graph.node(node_id, entity["type"], entity["name"],
                       file=entity["file"], line=entity["line"], **attrs)
            clang_keys.add((entity["type"], entity["name"]))
            added += 1

        resolved = 0
        unresolved = 0
        new_calls = 0
        clang_calls = {
            (edge["head"], edge["tail"])
            for edge in graph.edges.values()
            if edge["type"] == "CALLS"
        }
        for relation in self.relations:
            if relation["type"] != "CALLS":
                continue
            callee_name = relation.get("callee_name", "")
            tail = by_name.get(("FUNCTION", callee_name))
            caller_name = self.id_to_name.get(relation["head"], "")
            head = by_name.get(("FUNCTION", caller_name))
            if head is None:
                continue
            if tail is None:
                unresolved += 1
                continue
            if (head, tail) in clang_calls:
                resolved += 1
                continue
            graph.edge(head, tail, "CALLS", engine="tree-sitter", source="tree-sitter",
                       line=relation.get("line"), not_in_compiled_ast=True)
            clang_calls.add((head, tail))
            new_calls += 1
        return {"nodes_added": added, "dead_code_nodes": dead_code,
                "unmatched_nodes": unmatched, "ts_calls_resolved": resolved,
                "ts_calls_unmatched": unresolved, "ts_calls_new": new_calls}

    def summary(self) -> dict:
        return {
            "available": self.available,
            "entities": len(self.entities),
            "relations": len(self.relations),
            "counts": {k: v for k, v in self.counters.items() if v},
            "files": len(self.per_file),
        }
