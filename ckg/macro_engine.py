"""Macro definition/expansion modelling.

libclang can tell us *where* a macro is expanded and *which* macro definition a
site refers to, but it does not hand back the expanded text.  This module
reconstructs it from the token stream of the definition plus the arguments
written at the call site, which is what makes the knowledge graph able to talk
about "what the code looks like after the preprocessor ran".

For a byte-exact, whole-file expansion the tool additionally shells out to a
real clang driver (``clang -E`` / ``zig cc -E``) when one is available; see
:mod:`ckg.preprocess`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_DEPTH = 40
MAX_TOKENS = 4000


@dataclass
class MacroDef:
    name: str
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    is_function_like: bool
    params: list[str]
    tokens: list[tuple[str, bool]]  # (spelling, is_identifier) of the body only
    raw_text: str
    doc: str = ""

    @property
    def body_text(self) -> str:
        return " ".join(t[0] for t in self.tokens)


@dataclass
class Expansion:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    invocation: str  # exactly as written, e.g. MAX(custom_size, BUF_SIZE)
    macro: str  # macro name
    expanded: str
    arguments: list[str] = field(default_factory=list)
    depth: int = 0
    owner: str = ""  # enclosing function id
    generated: list[str] = field(default_factory=list)  # entity ids produced by it
    expanded_compiler: str = ""  # text produced by a real `clang -E` run

    def as_reference_record(self) -> dict:
        """Same shape as the macro.json used by code_kg_with_tree-sitter."""
        return {
            "file": self.file,
            "location": [self.line, self.column, self.end_line, self.end_column],
            "name": self.invocation,
            "macro": self.expanded,
        }


def _strip_comments(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith("//", i):
            j = text.find("\n", i + 2)
            i = n if j < 0 else j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def split_definition(tokens: list[tuple[str, bool]]) -> tuple:
    """Split a macro-definition token list into (params, body).

    ``tokens`` starts at the macro name.  For object-like macros the parameter
    list is empty and the whole remainder is the body.
    """
    if len(tokens) < 2:
        return [], []
    if tokens[1][0] != "(":
        return [], tokens[1:]
    depth = 0
    params: list[str] = []
    current: list[str] = []
    i = 1
    while i < len(tokens):
        text = tokens[i][0]
        if text == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        if text == ")":
            depth -= 1
            if depth == 0:
                if current:
                    params.append("".join(current).strip())
                return [p for p in params if p], tokens[i + 1 :]
        if depth == 1 and text == ",":
            params.append("".join(current).strip())
            current = []
        else:
            current.append(text)
        i += 1
    return [p for p in params if p], tokens[i:]


def split_call_arguments(tokens: list[tuple[str, bool]]) -> tuple:
    """Parse ``( a, b, c )`` starting at index 0 -> (args, index_after_paren)."""
    args: list[list[str]] = [[]]
    depth = 0
    i = 0
    while i < len(tokens):
        text = tokens[i][0]
        if text == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        if text == ")":
            depth -= 1
            if depth == 0:
                joined = ["".join(a).strip() for a in args]
                if len(joined) == 1 and not joined[0]:
                    joined = []
                return joined, i + 1
        if depth == 1 and text == ",":
            args.append([])
        else:
            args[-1].append(text)
        i += 1
    return ["".join(a).strip() for a in args], i


class MacroExpander:
    """Recursively expands macros using token-level substitution."""

    def __init__(self, definitions: dict[str, MacroDef], include_helpers: bool = True) -> None:
        self.defs = definitions
        self.include_helpers = include_helpers

    def _expand(
        self,
        tokens: list[tuple[str, bool]],
        active: frozenset,
        depth: int,
    ) -> list[str]:
        if depth > MAX_DEPTH:
            return [t[0] for t in tokens]
        out: list[str] = []
        i = 0
        while i < len(tokens):
            text, is_ident = tokens[i]
            definition = self.defs.get(text) if is_ident else None
            if definition is not None and text not in active and i + 1 <= len(tokens):
                if definition.is_function_like:
                    if i + 1 < len(tokens) and tokens[i + 1][0] == "(":
                        args, nxt = split_call_arguments(tokens[i + 1 :])
                        body = self._substitute(definition, args)
                        out.extend(
                            self._expand(body, active | {text}, depth + 1)
                        )
                        i += 1 + nxt
                        continue
                else:
                    out.extend(self._expand(definition.tokens, active | {text}, depth + 1))
                    i += 1
                    continue
            out.append(text)
            i += 1
            if len(out) > MAX_TOKENS:
                out.append("...")
                break
        return out

    def _substitute(self, definition: MacroDef, args: list[str]) -> list[tuple[str, bool]]:
        mapping = {}
        for idx, name in enumerate(definition.params):
            mapping[name] = args[idx] if idx < len(args) else ""
        args_by_name = {}
        for idx, name in enumerate(definition.params):
            raw = args[idx] if idx < len(args) else ""
            args_by_name[name] = [(tok, tok.isidentifier()) for tok in _retokenize(raw)]

        body = definition.tokens
        out: list[tuple[str, bool]] = []
        i = 0
        while i < len(body):
            text, is_ident = body[i]
            nxt = body[i + 1][0] if i + 1 < len(body) else ""
            # stringify
            if text == "#" and nxt in mapping:
                raw = mapping[nxt]
                out.append(('"%s"' % raw.replace("\\", "\\\\").replace('"', '\\"'), False))
                i += 2
                continue
            # token paste
            if nxt == "##" and (text in mapping or i + 2 < len(body)):
                right = body[i + 2][0] if i + 2 < len(body) else ""
                left_val = mapping.get(text, text)
                right_val = mapping.get(right, right)
                out.append((left_val + right_val, (left_val + right_val).isidentifier()))
                i += 3
                continue
            if is_ident and text in mapping:
                out.extend(args_by_name.get(text, []))
                i += 1
                continue
            out.append((text, is_ident))
            i += 1
        return out

    def expand_invocation(
        self, name: str, arg_tokens: list[tuple[str, bool]] | None, args_text: list[str] | None = None
    ) -> str:
        """Expand a macro as invoked at a call site."""
        definition = self.defs.get(name)
        if definition is None:
            return ""
        if definition.is_function_like:
            if args_text:
                body = self._substitute(definition, args_text)
            elif arg_tokens:
                body = self._substitute(
                    definition, ["".join(t[0] for t in arg_tokens)]
                )
            else:
                body = definition.tokens
        else:
            body = definition.tokens
        tokens = self._expand(body, frozenset({name}), 0)
        return _pretty_join(tokens)

    def expand_definition(self, name: str) -> str:
        definition = self.defs.get(name)
        if definition is None:
            return ""
        return _pretty_join(self._expand(definition.tokens, frozenset({name}), 0))

    def referenced_macros(self, definition: MacroDef) -> list[str]:
        """Macro names appearing (directly or nested) in a macro body."""
        found: list[str] = []
        for text, is_ident in definition.tokens:
            if is_ident and text in self.defs and text != definition.name and text not in found:
                found.append(text)
        return found


def _retokenize(text: str) -> list[str]:
    """Very small C tokenizer used only for macro arguments."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch in "()[]{},;":
            out.append(ch)
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\":
                    j += 1
                j += 1
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if ch.isalnum() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(text[i:j])
            i = j
            continue
        # punctuation, keep two-character operators together
        two = text[i : i + 2]
        if two in ("<<", ">>", "<=", ">=", "==", "!=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "->", "##"):
            out.append(two)
            i += 2
            continue
        out.append(ch)
        i += 1
    return out


def read_define_text(lines: list[str], line: int, column: int) -> str:
    """Join a (possibly backslash-continued) ``#define`` into one string.

    ``line``/``column`` point at the macro *name*; everything before it
    (``#define``) is dropped.
    """
    chunks: list[str] = []
    index = line - 1
    while 0 <= index < len(lines):
        text = lines[index]
        if index == line - 1:
            text = text[column - 1 :]
        text = text.rstrip()
        continued = text.endswith("\\")
        chunks.append(text[:-1] if continued else text)
        if not continued:
            break
        index += 1
    return " ".join(chunks)


def parse_define(
    lines: list[str], line: int, column: int, is_function_like: bool | None = None
) -> tuple[str, bool, list[str], list[tuple[str, bool]], str]:
    """Parse a ``#define`` into (name, is_function_like, params, body, raw)."""
    raw = read_define_text(lines, line, column)
    cleaned = _strip_comments(raw)
    tokens = _retokenize(cleaned)
    if not tokens:
        return "", False, [], [], raw
    name = tokens[0]
    rest = tokens[1:]
    if is_function_like is None:
        # a function-like macro has '(' immediately after the name in the source
        tail = cleaned[len(name) :]
        is_function_like = tail.startswith("(")
    if is_function_like:
        params, body = split_definition(tokens)
    else:
        params, body = [], rest
    return name, bool(is_function_like), params, [(t, t.isidentifier()) for t in body], raw


def _pretty_join(tokens: list[str]) -> str:
    """Join tokens with the spacing a human would expect."""
    out = ""
    for token in tokens:
        if not out:
            out = token
            continue
        if token in (")", "]", ",", ";", "->", ".") or out.endswith(("(", "[", "->", ".")):
            out += token
        elif token in ("(", "[") and _ends_identifier(out):
            out += token
        elif _ends_identifier(out) and _is_identifier(token):
            out += " " + token
        elif token in ("++", "--"):
            out += token
        else:
            out += " " + token
    return out


def _is_identifier(text: str) -> bool:
    return bool(text) and (text[0].isalpha() or text[0] == "_") and all(
        c.isalnum() or c == "_" for c in text
    )


def _ends_identifier(text: str) -> bool:
    return bool(text) and (text[-1].isalnum() or text[-1] == "_")
