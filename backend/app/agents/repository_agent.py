"""Agent 1 -- Repository Explorer.

Responsible for understanding the repository structure around this
commit: pulling full previous/current content for every changed file
(never just the diff hunk) and seeding the symbol index so later agents
can do fast local lookups instead of re-fetching from GitHub per tool
call.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from ..github.client import GitHubClient
from ..logging_config import get_logger

logger = get_logger(__name__)


class RepositoryExplorerAgent(Agent):
    name = "repository_explorer"

    def __init__(self, github_client: GitHubClient):
        self.github = github_client

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        ctx.log(self.name, f"Loading full previous/current content for {len(ctx.commit.files)} changed file(s)")

        self.github.populate_file_versions(ctx.owner, ctx.repo, ctx.commit)

        for f in ctx.commit.files:
            # GitHub can omit `patch` for very large per-file diffs; when
            # that happens we already have full content from
            # populate_file_versions above, so nothing is lost.
            if f.patch is None and f.previous_content is None and f.current_content is None:
                ctx.log(
                    self.name,
                    f"No patch or fetchable content for {f.filename} (likely binary or inaccessible)",
                    level="warning",
                )
            content_for_index = f.current_content or f.previous_content
            if content_for_index:
                ctx.extra_file_contents[f.filename] = content_for_index
                ctx.index.add_file(f.filename, content_for_index)

        ctx.log(
            self.name,
            f"Repository exploration complete: {ctx.index.stats()}",
        )
        return ctx
