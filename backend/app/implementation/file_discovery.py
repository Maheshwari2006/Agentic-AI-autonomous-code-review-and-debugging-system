"""
File discovery -- ranks a repository's files by relevance to a natural-
language implementation request, using multiple cheap signals *before*
any file content is fetched, then re-scores the top candidates using
real content signals (imports, defined symbols) once fetched.

This deliberately never returns "every file" (spec section 4). It is a
two-phase funnel:

  Phase 1 (path-only, no network beyond the tree fetch already done):
    score every file by filename/path token overlap with the request,
    plus structural boosts (tests, config, explicit target_file).

  Phase 2 (content-aware, only for the top `fetch_budget` phase-1
    candidates): fetch those files' content, extract imports/symbols via
    the existing AST layer, and re-rank using symbol-name / import
    overlap with the request in addition to the phase-1 path score.

The implementation agent (agents/implementation_agent.py) still has
tools to read/search *any* file beyond this shortlist -- this module
just produces a strong, explainable starting context so the agent
doesn't have to blindly enumerate the whole repository itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..github.file_fetcher import FileFetcher
from ..github.tree_parser import ParsedTree, RepoFile
from ..parsing.base import detect_language, get_parser
from ..parsing.python_ast import extract_imports

_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "in", "on", "and", "or", "with",
    "add", "implement", "create", "make", "update", "fix", "support",
    "should", "that", "this", "it", "so", "when", "using", "via", "into",
    "is", "are", "be", "as", "new", "existing",
}


def _tokenize(text: str) -> list:
    """Split identifiers/prose into lowercase word tokens, splitting on
    non-alphanumerics *and* camelCase boundaries, so "JWT authentication"
    matches both `jwt_utils.py` and `JWTMiddleware`."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


@dataclass
class ScoredFile:
    path: str
    score: float
    reasons: list = field(default_factory=list)
    is_test: bool = False
    is_config: bool = False
    matched_symbols: list = field(default_factory=list)
    matched_imports: list = field(default_factory=list)


def _fuzzy_overlap(path_tokens: set, request_tokens: set) -> set:
    """Catches stem/substring relationships exact-token overlap misses --
    e.g. path token 'auth' against request token 'authentication', or
    'token' against 'tokens'. Only applied to tokens of reasonable length
    so short common words don't spuriously match everything."""
    hits = set()
    for pt in path_tokens:
        if len(pt) < 4:
            continue
        for rt in request_tokens:
            if len(rt) < 4:
                continue
            if pt == rt:
                continue  # already covered by exact overlap
            if pt.startswith(rt) or rt.startswith(pt):
                hits.add(pt)
    return hits


def _path_score(file: RepoFile, request_tokens: set, explicit_target: Optional[str]) -> ScoredFile:
    path_tokens = set(_tokenize(file.path))
    overlap = path_tokens & request_tokens
    fuzzy = _fuzzy_overlap(path_tokens, request_tokens) - overlap
    score = 0.0
    reasons = []

    if explicit_target and file.path == explicit_target:
        score += 1000.0
        reasons.append("explicit target_file")

    if overlap:
        # Filename matches weigh more than deep-path-only matches.
        filename_tokens = set(_tokenize(file.path.rsplit("/", 1)[-1]))
        filename_overlap = overlap & filename_tokens
        score += 6.0 * len(filename_overlap)
        score += 2.0 * len(overlap - filename_overlap)
        reasons.append(f"path/filename token match: {sorted(overlap)}")

    if fuzzy:
        filename_tokens = set(_tokenize(file.path.rsplit("/", 1)[-1]))
        filename_fuzzy = fuzzy & filename_tokens
        score += 3.5 * len(filename_fuzzy)
        score += 1.0 * len(fuzzy - filename_fuzzy)
        reasons.append(f"path/filename related-word match: {sorted(fuzzy)}")

    if file.is_config and request_tokens & {"config", "configuration", "settings", "env", "environment"}:
        score += 3.0
        reasons.append("configuration file, request mentions configuration")

    if file.is_test:
        # Tests are relevant context but shouldn't outrank the real
        # implementation files just because names overlap; small boost
        # only, and only when there's already some path relevance.
        if overlap or fuzzy:
            score += 1.5
            reasons.append("related test file")

    # Shallower files (closer to repo root / a clearly-named package) are
    # slightly favored over deeply nested generated/vendor-like paths.
    score -= 0.1 * max(file.depth - 2, 0)

    return ScoredFile(path=file.path, score=score, reasons=reasons, is_test=file.is_test, is_config=file.is_config)


def discover_relevant_files(
    tree: ParsedTree,
    request_text: str,
    fetcher: FileFetcher,
    target_file: Optional[str] = None,
    phase1_top_n: int = 40,
    fetch_budget: int = 25,
    final_top_n: int = 20,
) -> list:
    """Returns a list[ScoredFile] for the most relevant files to
    `request_text`, sorted descending by score. `fetch_budget` bounds how
    many files phase 2 fetches content for (a retrieval-scope control,
    not a truncation of any individual file's content -- see config.py's
    `max_symbols_per_context_batch` for the analogous control on the
    review pipeline)."""
    request_tokens = set(_tokenize(request_text))

    # --- phase 1: path-only scoring ---
    phase1 = [
        _path_score(f, request_tokens, target_file)
        for f in tree.files
        if f.extension not in ("", "lock")
    ]
    phase1.sort(key=lambda sf: sf.score, reverse=True)

    # Always ensure the explicit target file is included even if its path
    # happened to score at zero on tokens.
    candidates = phase1[:phase1_top_n]
    if target_file and not any(c.path == target_file for c in candidates):
        candidates.append(_path_score(
            next((f for f in tree.files if f.path == target_file), RepoFile(target_file, "", 0, 0, False, False)),
            request_tokens, target_file,
        ))

    # --- phase 2: content-aware re-scoring for the strongest candidates ---
    to_fetch = [c.path for c in sorted(candidates, key=lambda sf: sf.score, reverse=True)[:fetch_budget]]
    contents = fetcher.get_many(to_fetch)

    for sf in candidates:
        content = contents.get(sf.path)
        if not content:
            continue
        language = detect_language(sf.path)
        parser = get_parser(language)
        if parser:
            try:
                symbols = parser(content)
            except Exception:
                symbols = []
            for sym in symbols:
                sym_tokens = set(_tokenize(sym.name))
                overlap = (sym_tokens & request_tokens) | _fuzzy_overlap(sym_tokens, request_tokens)
                if overlap:
                    sf.score += 4.0 * len(overlap)
                    sf.matched_symbols.append(sym.qualified_name)
        if language == "python":
            for imp in extract_imports(content):
                imp_tokens = set(_tokenize(imp))
                if (imp_tokens & request_tokens) or _fuzzy_overlap(imp_tokens, request_tokens):
                    sf.score += 1.5
                    sf.matched_imports.append(imp)

    candidates.sort(key=lambda sf: sf.score, reverse=True)
    # Keep anything with a nonzero score, up to final_top_n, plus always
    # keep the explicit target file regardless of score.
    ranked = [c for c in candidates if c.score > 0][:final_top_n]
    if target_file and not any(c.path == target_file for c in ranked):
        forced = next((c for c in candidates if c.path == target_file), None)
        if forced:
            ranked.insert(0, forced)
    return ranked
