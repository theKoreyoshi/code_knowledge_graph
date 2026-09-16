"""Source-file classification.

The extractor is not hard-wired to C: clang handles C, C++, Objective-C and
Objective-C++, and the tree-sitter fallback covers more grammars when the
corresponding wheel is installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    clang_kind: str
    tree_sitter: str
    is_header: bool = False

    @property
    def is_cpp(self) -> bool:
        return self.name.startswith("c++") or self.name.startswith("objective-c++")


C = Language("c", "c", "c")
C_HEADER = Language("c-header", "c-header", "c", is_header=True)
CPP = Language("c++", "c++", "cpp")
CPP_HEADER = Language("c++-header", "c++-header", "cpp", is_header=True)
OBJC = Language("objective-c", "objective-c", "objc")
OBJC_HEADER = Language("objective-c-header", "objective-c-header", "objc", is_header=True)
OBJCXX = Language("objective-c++", "objective-c++", "objc")
OBJCXX_HEADER = Language(
    "objective-c++-header", "objective-c++-header", "objc", is_header=True
)

_BY_EXTENSION = {
    ".c": C,
    ".h": C_HEADER,
    ".i": C,
    ".cc": CPP,
    ".cp": CPP,
    ".cpp": CPP,
    ".cxx": CPP,
    ".c++": CPP,
    ".cppm": CPP,
    ".ixx": CPP,
    ".ii": CPP,
    ".hpp": CPP_HEADER,
    ".hh": CPP_HEADER,
    ".hxx": CPP_HEADER,
    ".h++": CPP_HEADER,
    ".inl": CPP_HEADER,
    ".ipp": CPP_HEADER,
    ".tpp": CPP_HEADER,
    ".m": OBJC,
    ".mi": OBJC,
    ".mm": OBJCXX,
    ".mii": OBJCXX,
}

DEFAULT_EXTENSIONS = tuple(_BY_EXTENSION)
HEADER_EXTENSIONS = tuple(ext for ext, lang in _BY_EXTENSION.items() if lang.is_header)


def classify(path: str) -> Language | None:
    return _BY_EXTENSION.get(os.path.splitext(path)[1].lower())


def is_header(path: str) -> bool:
    language = classify(path)
    return bool(language and language.is_header)


def tree_sitter_grammar(language: Language):
    """Return a tree-sitter Language object, or None when unavailable."""
    if not language.tree_sitter:
        return None
    try:
        from tree_sitter import Language as TSLanguage

        if language.tree_sitter == "c":
            import tree_sitter_c

            return TSLanguage(tree_sitter_c.language())
        if language.tree_sitter == "cpp":
            import tree_sitter_cpp

            return TSLanguage(tree_sitter_cpp.language())
        if language.tree_sitter == "objc":
            import tree_sitter_objc

            return TSLanguage(tree_sitter_objc.language())
    except Exception:
        return None
    return None
