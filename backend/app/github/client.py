"""
GitHubClient -- thin, well-behaved wrapper around the GitHub REST API.

Deliberately built on `requests` (already a hard dependency) rather than a
heavier SDK, so it's easy to audit exactly which endpoints are called.

Endpoints used:
  GET /repos/{owner}/{repo}
  GET /repos/{owner}/{repo}/commits/{sha}
  GET /repos/{owner}/{repo}/commits?sha={branch}
  GET /repos/{owner}/{repo}/contents/{path}?ref={sha}
  GET /repos/{owner}/{repo}/pulls/{number}
  GET /repos/{owner}/{repo}/pulls/{number}/files

Design notes relevant to the "no fixed line-limit" requirement:
  - `get_commit()` returns GitHub's per-file `patch` field AS-IS, uncut.
    GitHub itself omits `patch` for very large diffs (>~3000 lines changed
    in a file); when that happens we detect the omission and fall back to
    fetching the full previous/current file content directly via the
    contents API so downstream AST analysis still has real source to work
    with, instead of silently operating on a truncated diff.
  - No `[:N]` slicing of diff/file text happens anywhere in this module.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from ..config import get_settings
from ..logging_config import get_logger

logger = get_logger(__name__)


class GitHubError(RuntimeError):
    """Raised for any GitHub API failure, with a human-actionable message."""


@dataclass
class FileChange:
    filename: str
    status: str  # added|modified|removed|renamed
    additions: int
    deletions: int
    patch: Optional[str]  # unified diff hunk text for this file, or None
    previous_filename: Optional[str] = None
    sha: Optional[str] = None
    previous_content: Optional[str] = None  # populated lazily
    current_content: Optional[str] = None  # populated lazily


@dataclass
class CommitDetail:
    sha: str
    message: str
    author_name: str
    author_email: str
    authored_at: str
    parent_sha: Optional[str]
    additions: int
    deletions: int
    files: list = field(default_factory=list)  # list[FileChange]
    html_url: str = ""


class GitHubClient:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        settings = get_settings()
        self.token = token if token is not None else settings.github_token
        self.base_url = base_url or settings.github_api_base
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # low-level request helper with retry/backoff + friendly errors
    # ------------------------------------------------------------------ #
    def _headers(self, accept: str = "application/vnd.github+json") -> dict:
        headers = {"Accept": accept, "User-Agent": "agentic-ai-code-review"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, url: str, *, accept: str = "application/vnd.github+json",
                 params: Optional[dict] = None, max_retries: int = 3) -> requests.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.request(
                    method, url, headers=self._headers(accept), params=params, timeout=30
                )
            except requests.RequestException as e:
                last_exc = e
                logger.warning(f"github_request_failed attempt={attempt} error={e}")
                time.sleep(min(2 ** attempt, 8))
                continue

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = resp.headers.get("X-RateLimit-Reset")
                raise GitHubError(
                    "GitHub API rate limit exceeded. Set GITHUB_TOKEN in your .env to raise "
                    f"the limit from 60/hour to 5000/hour. (reset header: {reset})"
                )
            if resp.status_code == 404:
                raise GitHubError(
                    f"GitHub resource not found (404) at {url}. Check the owner/repo/sha and, "
                    "for private repos, that GITHUB_TOKEN has access."
                )
            if resp.status_code == 401:
                raise GitHubError("GitHub authentication failed (401). Check GITHUB_TOKEN.")
            if resp.status_code >= 500:
                last_exc = GitHubError(f"GitHub server error {resp.status_code} at {url}")
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code >= 400:
                raise GitHubError(f"GitHub API error {resp.status_code} at {url}: {resp.text[:500]}")
            return resp

        raise GitHubError(f"GitHub API request failed after {max_retries} attempts: {last_exc}")

    # ------------------------------------------------------------------ #
    # repository / commit metadata
    # ------------------------------------------------------------------ #
    def get_repository(self, owner: str, repo: str) -> dict:
        url = f"{self.base_url}/repos/{owner}/{repo}"
        return self._request("GET", url).json()

    def list_commits(self, owner: str, repo: str, branch: Optional[str] = None,
                      per_page: int = 30, page: int = 1) -> list:
        url = f"{self.base_url}/repos/{owner}/{repo}/commits"
        params = {"per_page": per_page, "page": page}
        if branch:
            params["sha"] = branch
        return self._request("GET", url, params=params).json()

    def get_commit(self, owner: str, repo: str, sha: str) -> CommitDetail:
        """Fetch full commit detail: metadata + per-file patch text."""
        url = f"{self.base_url}/repos/{owner}/{repo}/commits/{sha}"
        data = self._request("GET", url).json()

        parents = data.get("parents", [])
        parent_sha = parents[0]["sha"] if parents else None
        commit_info = data.get("commit", {})
        author_info = commit_info.get("author", {}) or {}

        files = []
        for f in data.get("files", []) or []:
            files.append(
                FileChange(
                    filename=f.get("filename"),
                    status=f.get("status", "modified"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch"),  # may be None if GitHub omitted it (huge file)
                    previous_filename=f.get("previous_filename"),
                    sha=f.get("sha"),
                )
            )

        stats = data.get("stats", {}) or {}
        return CommitDetail(
            sha=data["sha"],
            message=commit_info.get("message", ""),
            author_name=author_info.get("name", ""),
            author_email=author_info.get("email", ""),
            authored_at=author_info.get("date", ""),
            parent_sha=parent_sha,
            additions=stats.get("additions", 0),
            deletions=stats.get("deletions", 0),
            files=files,
            html_url=data.get("html_url", ""),
        )

    # ------------------------------------------------------------------ #
    # file content at a given revision -- used both as a fallback when
    # GitHub omits `patch` for large files, and to give agents the full
    # original/new file for AST parsing (never just the diff hunk).
    # ------------------------------------------------------------------ #
    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> Optional[str]:
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        try:
            resp = self._request("GET", url, params={"ref": ref})
        except GitHubError as e:
            logger.info(f"file_content_unavailable path={path} ref={ref[:8]} reason={e}")
            return None
        data = resp.json()
        if isinstance(data, list):
            return None  # path is a directory
        content_b64 = data.get("content")
        if content_b64 is None:
            return None
        if data.get("encoding") == "base64":
            try:
                return base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:
                return None
        return content_b64

    def populate_file_versions(self, owner: str, repo: str, commit: CommitDetail) -> None:
        """Fill in previous_content / current_content for every changed file.
        This is what lets the AST layer work on the *whole* file rather than
        just the diff hunk, with no line-count ceiling."""
        for f in commit.files:
            if f.status != "added" and commit.parent_sha:
                f.previous_content = self.get_file_content(owner, repo, f.filename, commit.parent_sha)
            if f.status != "removed":
                f.current_content = self.get_file_content(owner, repo, f.filename, commit.sha)

    # ------------------------------------------------------------------ #
    # pull requests (optional entry point alongside raw commits)
    # ------------------------------------------------------------------ #
    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}"
        return self._request("GET", url).json()

    def get_pull_request_commits(self, owner: str, repo: str, number: int) -> list:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}/commits"
        return self._request("GET", url).json()

    def get_default_branch(self, owner: str, repo: str) -> str:
        return self.get_repository(owner, repo).get("default_branch", "main")

    # ------------------------------------------------------------------ #
    # ref resolution + full repository tree -- used by the implementation
    # feature to browse a repository at a branch or commit SHA rather than
    # only at a single commit's diff (see github/repository_fetcher.py,
    # github/tree_parser.py, github/file_fetcher.py).
    # ------------------------------------------------------------------ #
    def resolve_ref(self, owner: str, repo: str, ref: Optional[str] = None) -> str:
        """Resolve a branch name, tag, or (possibly short) commit SHA to a
        full 40-char commit SHA. `ref=None` resolves the default branch."""
        ref = ref or self.get_default_branch(owner, repo)
        url = f"{self.base_url}/repos/{owner}/{repo}/commits/{ref}"
        data = self._request("GET", url).json()
        sha = data.get("sha")
        if not sha:
            raise GitHubError(f"Could not resolve ref '{ref}' to a commit SHA.")
        return sha

    def get_tree(self, owner: str, repo: str, ref: str, recursive: bool = True) -> list:
        """Return the full repository file tree at `ref` using the Git
        Trees API (one request for the whole tree, recursive=1) rather
        than walking directories one-by-one via the Contents API. Each
        entry: {"path", "type" ("blob"|"tree"), "sha", "size"}.

        GitHub truncates this response (`truncated: true`) only for
        genuinely enormous trees (>100k entries / >7MB payload); when that
        happens we log it rather than silently pretending the tree is
        complete -- callers (file discovery) should fall back to directory
        listing for the specific subtrees they care about in that case.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/git/trees/{ref}"
        params = {"recursive": "1"} if recursive else None
        data = self._request("GET", url, params=params).json()
        if data.get("truncated"):
            logger.warning(
                f"github_tree_truncated owner={owner} repo={repo} ref={ref[:8]} "
                "-- repository is very large; consider directory-scoped fetches."
            )
        return [
            {"path": e["path"], "type": e["type"], "sha": e.get("sha"), "size": e.get("size")}
            for e in data.get("tree", [])
            if e.get("type") in ("blob", "tree")
        ]

    def list_directory(self, owner: str, repo: str, path: str, ref: str) -> list:
        """Non-recursive directory listing via the Contents API. Used as a
        fallback for subtrees of a truncated (very large) repository tree."""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        data = self._request("GET", url, params={"ref": ref}).json()
        if not isinstance(data, list):
            return []
        return [
            {"path": e["path"], "type": "tree" if e.get("type") == "dir" else "blob",
             "sha": e.get("sha"), "size": e.get("size")}
            for e in data
        ]

    def get_clone_url(self, owner: str, repo: str) -> str:
        if self.token:
            return f"https://{self.token}@github.com/{owner}/{repo}.git"
        return f"https://github.com/{owner}/{repo}.git"
