"""
Language-agnostic symbol model. Every language-specific parser (currently
just Python; see python_ast.py) produces a list of `Symbol` objects using
this same shape, so the rest of the system (change detection, dependency
analysis, agents) never needs to know which language it's looking at.

To add a new language: implement a parser with the same
`extract_symbols(source: str) -> list[Symbol]` signature and register it
in LANGUAGE_PARSERS below, keyed by file extension.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Symbol:
    name: str
    qualified_name: str  # e.g. "ClassName.method_name" or just "func_name"
    symbol_type: str  # "function" | "method" | "class"
    start_line: int
    end_line: int
    source: str
    parent: Optional[str] = None  # enclosing class name, if any
    calls: list = field(default_factory=list)  # names this symbol calls
    decorators: list = field(default_factory=list)
    docstring: Optional[str] = None
    args: list = field(default_factory=list)


def detect_language(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python",
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "java": "java",
        "go": "go",
        "cpp": "cpp",
        "cc": "cpp",
        "c": "c",
        "h": "cpp",
        "hpp": "cpp",
    }.get(ext, "unknown")


def get_parser(language: str):
    """Returns the extract_symbols callable for a language, or None if
    that language isn't supported yet. Keeping this indirection (rather
    than importing every parser everywhere) is what makes it possible to
    add JS/TS/Java/Go/C++ parsers later without touching callers."""
    if language == "python":
        from .python_ast import extract_symbols

        return extract_symbols
    return None
