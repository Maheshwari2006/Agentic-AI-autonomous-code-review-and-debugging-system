"""
RepositoryFetcher -- repository/ref-level operations for the implementation
feature, built on top of the existing GitHubClient (github/client.py). This
module does not duplicate GitHubClient's HTTP/retry/error-handling logic; it
composes it into the specific workflow the implementation feature needs:

    resolve owner/repo/ref -> full repo tree -> parsed+filtered file list

Kept separate from `client.py` (which is the generic, low-level GitHub
wrapper reused by the existing commit-review pipeline) so the
implementation feature has its own small surface area, per the spec's
file layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .client import GitHubClient, GitHubError
from .tree_parser import ParsedTree, parse_tree
from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RepositoryHandle:
    owner: str
    repo: str
    ref: str  # as the caller specified it (branch name, tag, or SHA)
    resolved_sha: str  # full 40-char commit SHA the ref resolved to
    default_branch: str
    html_url: str
    tree: ParsedTree


class RepositoryFetcher:
    def __init__(self, github_client: Optional[GitHubClient] = None):
        self.github = github_client or GitHubClient()

    def load(self, owner: str, repo: str, ref: Optional[str] = None) -> RepositoryHandle:
        """Resolve `ref` (branch, tag, or commit SHA -- or None for the
        default branch) to a concrete commit and fetch+parse the full
        repository tree at that commit. This is the entry point every
        other implementation-feature module builds on."""
        info = self.github.get_repository(owner, repo)
        default_branch = info.get("default_branch", "main")

        try:
            resolved_sha = self.github.resolve_ref(owner, repo, ref or default_branch)
        except GitHubError:
            raise

        raw_tree = self.github.get_tree(owner, repo, resolved_sha, recursive=True)
        parsed = parse_tree(raw_tree)

        logger.info(
            f"repository_loaded owner={owner} repo={repo} ref={ref or default_branch} "
            f"resolved_sha={resolved_sha[:8]} files={len(parsed.files)} dirs={len(parsed.directories)}"
        )

        return RepositoryHandle(
            owner=owner,
            repo=repo,
            ref=ref or default_branch,
            resolved_sha=resolved_sha,
            default_branch=default_branch,
            html_url=info.get("html_url", f"https://github.com/{owner}/{repo}"),
            tree=parsed,
        )

    def changed_files_between(self, owner: str, repo: str, base_sha: str, head_sha: str) -> list:
        """Files that differ between two commits (used when the caller
        supplies both a base and a target ref, e.g. comparing a feature
        branch to main). Uses the GitHub compare API."""
        url = f"{self.github.base_url}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
        data = self.github._request("GET", url).json()
        return [
            {
                "filename": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            }
            for f in data.get("files", []) or []
        ]
