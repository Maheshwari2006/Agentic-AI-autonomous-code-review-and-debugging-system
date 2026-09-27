"""
FileFetcher -- lazy, per-repository-load content fetching with an
in-memory cache.

The implementation feature never downloads every file in a repository
(that would be wasteful and, for large repos, slow/rate-limit-heavy).
Instead file_discovery.py picks a ranked shortlist of *paths* from the
tree (tree_parser.py's output -- path/size/metadata only, no content),
and only those files' content is actually fetched, on demand, through
this class. Each path is fetched at most once per FileFetcher instance
(commonly one per implementation request).
"""
from __future__ import annotations

from typing import Optional

from .client import GitHubClient, GitHubError
from ..logging_config import get_logger

logger = get_logger(__name__)


class FileFetcher:
    def __init__(self, github_client: GitHubClient, owner: str, repo: str, ref: str):
        self.github = github_client
        self.owner = owner
        self.repo = repo
        self.ref = ref
        self._cache: dict = {}  # path -> content or None (fetch attempted, unavailable)

    def get(self, path: str) -> Optional[str]:
        if path in self._cache:
            return self._cache[path]
        try:
            content = self.github.get_file_content(self.owner, self.repo, path, self.ref)
        except GitHubError as e:
            logger.info(f"file_fetch_failed path={path} error={e}")
            content = None
        self._cache[path] = content
        return content

    def get_many(self, paths: list) -> dict:
        """Fetch several files, returning only the ones that were
        actually retrievable (binary/missing files are silently omitted,
        not padded with None -- callers iterate a dict of real content)."""
        out = {}
        for path in paths:
            content = self.get(path)
            if content is not None:
                out[path] = content
        return out

    def cached_paths(self) -> list:
        return [p for p, c in self._cache.items() if c is not None]

    def stats(self) -> dict:
        fetched = [p for p, c in self._cache.items() if c is not None]
        failed = [p for p, c in self._cache.items() if c is None]
        return {"fetched": len(fetched), "unavailable": len(failed)}
