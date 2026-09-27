"""
Symbol-level change detection.

Given the full previous-version source and full current-version source of
a file, this diffs them at the *symbol* level (function/method/class),
not the line level. Output is a list of ChangedSymbolInfo -- exactly which
named functions/classes were added, removed, or modified, each carrying
its full before/after source (no truncation).

This is what lets a 10,000-line file with two changed functions produce
exactly two (or a handful more, once dependencies are pulled in)
`ChangedSymbolInfo` records instead of a diff blob.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..parsing.base import Symbol, detect_language, get_parser
from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ChangedSymbolInfo:
    file_path: str
    symbol_name: str
    qualified_name: str
    symbol_type: str
    change_type: str  # added | removed | modified
    previous_symbol: Optional[Symbol]
    new_symbol: Optional[Symbol]

    @property
    def start_line(self) -> Optional[int]:
        s = self.new_symbol or self.previous_symbol
        return s.start_line if s else None

    @property
    def end_line(self) -> Optional[int]:
        s = self.new_symbol or self.previous_symbol
        return s.end_line if s else None


def _safe_extract(source: Optional[str], language: str) -> dict:
    """Returns {qualified_name: Symbol}. Never raises -- a file that fails
    to parse (e.g. genuinely broken syntax in a commit, or a language we
    don't support yet) degrades to "no symbols detected" rather than
    crashing the whole pipeline; the caller can fall back to whole-file
    review for that file."""
    if not source:
        return {}
    parser = get_parser(language)
    if parser is None:
        return {}
    try:
        symbols = parser(source)
    except Exception as e:
        logger.warning(f"symbol_extraction_failed language={language} error={e}")
        return {}
    return {s.qualified_name: s for s in symbols}


def detect_changed_symbols(
    file_path: str, previous_content: Optional[str], current_content: Optional[str]
) -> list:
    """Diff previous vs. current file content at the symbol level."""
    language = detect_language(file_path)
    prev_symbols = _safe_extract(previous_content, language)
    new_symbols = _safe_extract(current_content, language)

    changes: list = []
    all_names = set(prev_symbols) | set(new_symbols)

    for qname in sorted(all_names):
        prev_sym = prev_symbols.get(qname)
        new_sym = new_symbols.get(qname)

        if prev_sym is None and new_sym is not None:
            changes.append(
                ChangedSymbolInfo(file_path, new_sym.name, qname, new_sym.symbol_type,
                                   "added", None, new_sym)
            )
        elif prev_sym is not None and new_sym is None:
            changes.append(
                ChangedSymbolInfo(file_path, prev_sym.name, qname, prev_sym.symbol_type,
                                   "removed", prev_sym, None)
            )
        elif prev_sym is not None and new_sym is not None:
            if prev_sym.source.strip() != new_sym.source.strip():
                changes.append(
                    ChangedSymbolInfo(file_path, new_sym.name, qname, new_sym.symbol_type,
                                       "modified", prev_sym, new_sym)
                )
    return changes


def detect_changed_symbols_no_parser(file_path: str, patch: Optional[str]) -> list:
    """Fallback used when a file's language has no AST parser yet, or
    both previous/current full content are unavailable (e.g. binary,
    or GitHub omitted content for a huge file and the raw-content fetch
    also failed). We still surface the change as a single file-level
    'unit', flagged so agents/report know it wasn't symbol-decomposed,
    rather than silently dropping it."""
    if not patch:
        return []
    return [
        ChangedSymbolInfo(
            file_path=file_path,
            symbol_name="<whole file>",
            qualified_name=f"{file_path}::<whole file>",
            symbol_type="file",
            change_type="modified",
            previous_symbol=None,
            new_symbol=None,
        )
    ]


# --------------------------------------------------------------------- #
# Logical grouping: cluster changed symbols across files into independent
# "change groups" using shared file / call relationships, per the
# "multiple independent changes in one commit" requirement.
# --------------------------------------------------------------------- #

def group_changes(changed: list) -> list:
    """
    Groups a flat list of ChangedSymbolInfo into logical change units using
    union-find over two relations:
      1. same file -> same group (a file's changes are rarely independent
         of each other structurally)
      2. symbol A calls symbol B (or vice versa) among the changed set ->
         same group

    Returns: list of groups, each a list of ChangedSymbolInfo, ordered by
    group size descending (biggest/most central change first).
    """
    n = len(changed)
    if n == 0:
        return []

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    by_file: dict = {}
    for idx, c in enumerate(changed):
        by_file.setdefault(c.file_path, []).append(idx)
    for idxs in by_file.values():
        for k in range(1, len(idxs)):
            union(idxs[0], idxs[k])

    name_to_idx: dict = {}
    for idx, c in enumerate(changed):
        name_to_idx.setdefault(c.symbol_name, []).append(idx)

    for idx, c in enumerate(changed):
        sym = c.new_symbol or c.previous_symbol
        if not sym:
            continue
        for called_name in sym.calls:
            for other_idx in name_to_idx.get(called_name, []):
                if other_idx != idx:
                    union(idx, other_idx)

    groups: dict = {}
    for idx in range(n):
        root = find(idx)
        groups.setdefault(root, []).append(changed[idx])

    ordered_groups = sorted(groups.values(), key=len, reverse=True)
    return ordered_groups
