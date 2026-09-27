"""
AnalysisService -- the glue between the FastAPI layer, the GitHub client,
the agent pipeline, and the database.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from ..database import models
from ..github.client import GitHubClient, GitHubError
from ..llm.anthropic_provider import AnthropicProvider
from ..agents.base import AnalysisContext
from ..agents.orchestrator import AgentPipeline
from ..logging_config import get_logger

logger = get_logger(__name__)


def _parse_dt(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def get_or_create_repository(db: Session, owner: str, repo: str, github: GitHubClient) -> models.Repository:
    existing = (
        db.query(models.Repository)
        .filter(models.Repository.owner == owner, models.Repository.name == repo)
        .first()
    )
    if existing:
        return existing

    info = github.get_repository(owner, repo)
    record = models.Repository(
        owner=owner,
        name=repo,
        url=info.get("html_url", f"https://github.com/{owner}/{repo}"),
        default_branch=info.get("default_branch", "main"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


class AnalysisService:
    def __init__(self, db: Session, github_client: Optional[GitHubClient] = None,
                 llm_provider=None, run_verification: bool = True):
        self.db = db
        self.github = github_client or GitHubClient()
        self.llm = llm_provider or AnthropicProvider()
        self.run_verification = run_verification

    def analyze_commit(self, owner: str, repo: str, sha: str) -> models.Review:
        repo_record = get_or_create_repository(self.db, owner, repo, self.github)

        try:
            commit_detail = self.github.get_commit(owner, repo, sha)
        except GitHubError as e:
            logger.error(f"commit_fetch_failed owner={owner} repo={repo} sha={sha} error={e}")
            raise

        commit_record = (
            self.db.query(models.Commit)
            .filter(models.Commit.repository_id == repo_record.id, models.Commit.sha == commit_detail.sha)
            .first()
        )
        if commit_record is None:
            commit_record = models.Commit(
                repository_id=repo_record.id,
                sha=commit_detail.sha,
                parent_sha=commit_detail.parent_sha,
                message=commit_detail.message,
                author=commit_detail.author_name,
                author_email=commit_detail.author_email,
                authored_at=_parse_dt(commit_detail.authored_at),
                additions=commit_detail.additions,
                deletions=commit_detail.deletions,
                files_changed=len(commit_detail.files),
            )
            self.db.add(commit_record)
            self.db.commit()
            self.db.refresh(commit_record)

        ctx = AnalysisContext(owner=owner, repo=repo, commit=commit_detail)
        pipeline = AgentPipeline(self.github, self.llm, run_verification=self.run_verification)
        ctx = pipeline.run(ctx)

        return self._persist(commit_record, ctx)

    def _persist(self, commit_record: models.Commit, ctx: AnalysisContext) -> models.Review:
        # Persist changed files + symbols (idempotent-ish: this prototype
        # creates a fresh row set per analysis run rather than diffing
        # against a previous run's rows).
        for f in ctx.commit.files:
            file_record = models.ChangedFile(
                commit_id=commit_record.id,
                path=f.filename,
                status=f.status,
                additions=f.additions,
                deletions=f.deletions,
                previous_content=f.previous_content,
                current_content=f.current_content,
                patch=f.patch,
                language=_language_for(f.filename),
            )
            self.db.add(file_record)
            self.db.flush()

            for cs in ctx.changed_symbols:
                if cs.file_path != f.filename:
                    continue
                self.db.add(
                    models.ChangedSymbol(
                        file_id=file_record.id,
                        symbol_name=cs.symbol_name,
                        qualified_name=cs.qualified_name,
                        symbol_type=cs.symbol_type,
                        change_type=cs.change_type,
                        start_line=cs.start_line,
                        end_line=cs.end_line,
                        previous_source=cs.previous_symbol.source if cs.previous_symbol else None,
                        new_source=cs.new_symbol.source if cs.new_symbol else None,
                    )
                )

        review = models.Review(
            commit_id=commit_record.id,
            status=ctx.review_status or "PENDING",
            summary=ctx.review_summary,
            confidence=ctx.review_confidence,
            change_groups=[[c.qualified_name for c in g] for g in ctx.change_groups],
            agent_trace=ctx.trace,
        )
        self.db.add(review)
        self.db.flush()

        for issue in ctx.issues:
            self.db.add(
                models.Issue(
                    review_id=review.id,
                    file=issue.get("file", ""),
                    function=issue.get("function"),
                    line=issue.get("line"),
                    severity=str(issue.get("severity", "MEDIUM")).upper(),
                    category=str(issue.get("category", "CORRECTNESS")).upper(),
                    title=issue.get("title", ""),
                    description=issue.get("description", ""),
                    evidence=issue.get("evidence", ""),
                    impact=issue.get("impact", ""),
                    suggested_fix_explanation=issue.get("suggested_fix_explanation", ""),
                    suggested_patch=issue.get("suggested_patch", ""),
                    confidence=float(issue.get("confidence", 0.0) or 0.0),
                    tests_required=issue.get("tests_required", ""),
                )
            )

        for v in ctx.verification_results:
            self.db.add(
                models.Verification(
                    review_id=review.id,
                    status=v.get("status", "UNABLE_TO_VERIFY"),
                    tests_passed=v.get("tests_passed", 0),
                    tests_failed=v.get("tests_failed", 0),
                    details=v.get("details", ""),
                    command_run=v.get("command_run", ""),
                )
            )

        self.db.commit()
        self.db.refresh(review)
        return review


def _language_for(path: str) -> str:
    from ..parsing.base import detect_language

    return detect_language(path)
