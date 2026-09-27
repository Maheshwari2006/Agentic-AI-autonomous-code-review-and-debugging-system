"""Agent 3 -- Dependency Analyzer.

For every changed symbol, tries to widen the index with plausibly-related
files that were NOT themselves touched by the commit but that the review
agent will likely need: same-named test files, and (best-effort) any
other file already known to the index whose symbols call or are called by
a changed symbol. Real cross-repository-tree search (walking the full
repo via the Git Trees API) is left as a documented extension point --
see README "Limitations" -- since it requires an extra, potentially
expensive, full-tree fetch per commit; the prototype instead targets the
specific test-file naming conventions most Python (and JS/TS) projects
use, which covers the common case cheaply.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from ..github.client import GitHubClient
from ..logging_config import get_logger

logger = get_logger(__name__)


def _candidate_test_paths(file_path: str) -> list:
    import os

    directory = os.path.dirname(file_path)
    base = os.path.basename(file_path)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    ext = base.rsplit(".", 1)[-1] if "." in base else "py"

    candidates = [
        f"test_{stem}.{ext}",
        f"{stem}_test.{ext}",
        f"tests/test_{stem}.{ext}",
        f"{directory}/test_{stem}.{ext}" if directory else f"test_{stem}.{ext}",
        f"{directory}/tests/test_{stem}.{ext}" if directory else f"tests/test_{stem}.{ext}",
    ]
    seen = set()
    ordered = []
    for c in candidates:
        c = c.lstrip("/")
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


class DependencyAnalyzerAgent(Agent):
    name = "dependency_analyzer"

    def __init__(self, github_client: GitHubClient):
        self.github = github_client

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        changed_paths = {f.filename for f in ctx.commit.files}
        fetched = 0

        for f in ctx.commit.files:
            for candidate in _candidate_test_paths(f.filename):
                if candidate in changed_paths or candidate in ctx.extra_file_contents:
                    continue
                content = self.github.get_file_content(ctx.owner, ctx.repo, candidate, ctx.commit.sha)
                if content:
                    ctx.extra_file_contents[candidate] = content
                    ctx.index.add_file(candidate, content)
                    fetched += 1
                    ctx.log(self.name, f"Linked likely test file: {candidate}")

        caller_callee_notes = []
        for group in ctx.change_groups:
            for c in group:
                if c.change_type == "removed":
                    continue
                callers = ctx.index.find_callers(c.symbol_name)
                external_callers = [
                    cal for cal in callers if cal.file_path not in changed_paths
                ]
                if external_callers:
                    caller_callee_notes.append(
                        f"{c.symbol_name} is also called from {len(external_callers)} "
                        f"file(s) not part of this commit -- potential blast radius."
                    )

        ctx.log(
            self.name,
            f"Dependency analysis complete: fetched {fetched} related file(s); "
            f"{len(caller_callee_notes)} cross-file usage note(s).",
            notes=caller_callee_notes[:10],
        )
        return ctx
