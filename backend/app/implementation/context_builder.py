"""
Context builder for the implementation feature.

Builds the hierarchical, targeted context described in spec section 6:

    Repository -> files -> modules -> classes -> functions -> dependencies -> tests

Rather than truncating any single relevant file, this module indexes each
relevant file's *symbols* via the existing AST layer (parsing/python_ast.py)
and, for any symbol whose name matched the request, pulls in its callers
and callees from analysis/dependency.py's SymbolIndex -- exactly the same
retrieval strategy the existing review pipeline uses
(analysis/dependency.py's build_context_bundle), reused rather than
reimplemented. Related test files are attached by naming convention via
the same tests-for-file logic pattern used in agents/tools.py.

The number of *related* (caller/callee) symbols attached per matched
symbol is bounded by settings.max_symbols_per_context_batch -- a batching
control on how much dependency context accompanies one prompt, not a
truncation of any file or symbol's own source text (see config.py).
"""
from __future__ import annotations

import os

from .context import ImplementationContext
from ..analysis.dependency import build_context_bundle
from ..config import get_settings
from ..parsing.base import detect_language, get_parser
from ..logging_config import get_logger

logger = get_logger(__name__)


def _find_related_tests(path: str, all_paths: set) -> list:
    base = os.path.basename(path)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    candidates = {f"test_{stem}.py", f"{stem}_test.py"}
    found = []
    for p in all_paths:
        pbase = os.path.basename(p)
        if pbase in candidates or (stem and stem in pbase and pbase != base):
            found.append(p)
    return found


def build_implementation_context(ctx: ImplementationContext) -> dict:
    """Populates ctx.index and ctx.context_bundle, and returns the bundle.

    Structure of the returned bundle:
    {
      "request": str,
      "repository": {owner, repo, ref, resolved_sha, default_branch},
      "files": [
        {"path", "language", "content", "symbols": [...], "is_test", "is_config", "relevance_reasons"}
      ],
      "dependency_context": {matched_symbol_qualified_name: {"callers": [...], "callees": [...]}},
      "related_tests": {path: content},
      "config_files": {path: content},
    }
    """
    settings = get_settings()
    all_relevant_paths = {sf.path for sf in ctx.relevant_files}
    files_bundle = []
    matched_symbol_names = set()
    config_files = {}

    for sf in ctx.relevant_files:
        content = ctx.fetcher.get(sf.path)
        if content is None:
            ctx.log("context_builder", f"Skipping {sf.path}: content unavailable (binary or fetch failed)")
            continue

        language = detect_language(sf.path)
        symbols = ctx.index.add_file(sf.path, content)

        if sf.is_config:
            config_files[sf.path] = content

        files_bundle.append({
            "path": sf.path,
            "language": language,
            "content": content,
            "symbols": [
                {
                    "name": s.name,
                    "qualified_name": s.qualified_name,
                    "symbol_type": s.symbol_type,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "parent": s.parent,
                }
                for s in symbols
            ],
            "is_test": sf.is_test,
            "is_config": sf.is_config,
            "relevance_score": round(sf.score, 2),
            "relevance_reasons": sf.reasons,
        })

        for name in sf.matched_symbols:
            matched_symbol_names.add((sf.path, name))

    # If a specific target symbol was named, make sure it's tracked too.
    if ctx.target_file and ctx.target_symbol:
        matched_symbol_names.add((ctx.target_file, ctx.target_symbol))

    dependency_context = {}
    for file_path, qualified_name in matched_symbol_names:
        entry = ctx.index.get(file_path, qualified_name)
        symbol_name = entry.symbol.name if entry else qualified_name.rsplit(".", 1)[-1]
        bundle = build_context_bundle(
            ctx.index, symbol_name, file_path, qualified_name,
            max_related=settings.max_symbols_per_context_batch,
        )
        if bundle["callers"] or bundle["callees"]:
            dependency_context[f"{file_path}::{qualified_name}"] = bundle

    related_tests = {}
    for sf in ctx.relevant_files:
        if sf.is_test:
            continue
        for test_path in _find_related_tests(sf.path, all_relevant_paths):
            content = ctx.fetcher.get(test_path)
            if content:
                related_tests[test_path] = content
    # Also pick up test files already present in the discovered set.
    for f in files_bundle:
        if f["is_test"]:
            related_tests[f["path"]] = f["content"]

    bundle = {
        "request": ctx.request_text,
        "repository": {
            "owner": ctx.owner,
            "repo": ctx.repo,
            "ref": ctx.repo_handle.ref,
            "resolved_sha": ctx.repo_handle.resolved_sha,
            "default_branch": ctx.repo_handle.default_branch,
        },
        "target_file": ctx.target_file,
        "target_symbol": ctx.target_symbol,
        "files": files_bundle,
        "dependency_context": dependency_context,
        "related_tests": related_tests,
        "config_files": config_files,
    }
    ctx.context_bundle = bundle
    ctx.log(
        "context_builder",
        f"Context built: {len(files_bundle)} file(s), {len(dependency_context)} dependency bundle(s), "
        f"{len(related_tests)} related test(s).",
    )
    return bundle
