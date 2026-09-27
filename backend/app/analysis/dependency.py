"""
Repository-aware symbol index + dependency resolution.

This is the "retrieval layer" referenced throughout the spec: rather than
sending whole files to the LLM, we index every file's symbols once, then
answer targeted questions like "who calls login()?" or "what does
validate_token() depend on?" by looking the answer up in the index -- an
O(1)-ish dictionary lookup -- instead of re-scanning the whole repository
or re-sending it to the model.

For a prototype without a full multi-language cross-file resolver, calls
are matched by *name* (a callee named `validate_token` in any indexed
file is considered a candidate caller/callee of a symbol that calls/／is
called `validate_token`). This is intentionally conservative and
documented as a simplification in the README: it will over-match same-
named functions across unrelated modules in very large repos. A follow-up
enhancement (also noted in the README) is proper import-resolution-based
linking using the `extract_imports` data already collected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..parsing.base import Symbol, detect_language, get_parser


@dataclass
class IndexedSymbol:
    file_path: str
    symbol: Symbol


class SymbolIndex:
    """In-memory index of every symbol across a set of files (typically:
    every file present in the commit's repository tree, or --- for the
    prototype's targeted-retrieval mode --- just the files touched by the
    commit plus any files explicitly requested by the dependency agent).
    """

    def __init__(self):
        self._by_name: dict = {}  # symbol_name -> list[IndexedSymbol]
        self._by_qualified: dict = {}  # "path::qualified_name" -> IndexedSymbol
        self._files_indexed: set = set()

    def add_file(self, file_path: str, content: Optional[str]) -> list:
        if not content or file_path in self._files_indexed:
            return []
        language = detect_language(file_path)
        parser = get_parser(language)
        if parser is None:
            return []
        try:
            symbols = parser(content)
        except Exception:
            return []
        self._files_indexed.add(file_path)
        for sym in symbols:
            entry = IndexedSymbol(file_path, sym)
            self._by_name.setdefault(sym.name, []).append(entry)
            self._by_qualified[f"{file_path}::{sym.qualified_name}"] = entry
        return symbols

    def find_by_name(self, name: str) -> list:
        return self._by_name.get(name, [])

    def get(self, file_path: str, qualified_name: str) -> Optional[IndexedSymbol]:
        return self._by_qualified.get(f"{file_path}::{qualified_name}")

    def find_callers(self, symbol_name: str) -> list:
        """Every indexed symbol whose body calls `symbol_name`."""
        callers = []
        for entries in self._by_name.values():
            for entry in entries:
                if symbol_name in entry.symbol.calls:
                    callers.append(entry)
        return callers

    def find_callees(self, file_path: str, qualified_name: str) -> list:
        """Every indexed symbol that `qualified_name`'s body calls."""
        entry = self.get(file_path, qualified_name)
        if not entry:
            return []
        callees = []
        for called_name in entry.symbol.calls:
            callees.extend(self.find_by_name(called_name))
        return callees

    def stats(self) -> dict:
        return {
            "files_indexed": len(self._files_indexed),
            "symbols_indexed": sum(len(v) for v in self._by_name.values()),
        }


def build_context_bundle(
    index: SymbolIndex, changed_symbol_name: str, changed_file: str,
    changed_qualified_name: str, max_related: int = 15,
) -> dict:
    """Assemble the targeted context for one changed symbol: its callers
    and callees, each as full source text. `max_related` bounds how many
    *related* (not the changed symbol itself) items are attached to a
    single review call -- if a function has hundreds of callers, the
    review agent can request the rest via the find_callers tool in
    further calls rather than everything being forced into one prompt.
    This is a context-batching control, not a line/character truncation
    of any individual symbol's source.
    """
    callers = index.find_callers(changed_symbol_name)[:max_related]
    callees = index.find_callees(changed_file, changed_qualified_name)[:max_related]
    return {
        "callers": [
            {"file": c.file_path, "name": c.symbol.qualified_name, "source": c.symbol.source}
            for c in callers
        ],
        "callees": [
            {"file": c.file_path, "name": c.symbol.qualified_name, "source": c.symbol.source}
            for c in callees
        ],
    }
