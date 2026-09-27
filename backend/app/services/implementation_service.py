"""
ImplementationService -- glue between the FastAPI layer, the GitHub
repository fetcher, the file-discovery + context-building + agent
pipeline, and the database. Mirrors services/analysis_service.py's role
for the existing commit-review pipeline.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..database import models
from ..github.client import GitHubClient, GitHubError
from ..github.file_fetcher import FileFetcher
from ..github.repository_fetcher import RepositoryFetcher
from ..llm.anthropic_provider import AnthropicProvider
from ..agents.implementation_agent import ImplementationAgent
from ..implementation.context import ImplementationContext
from ..implementation.file_discovery import discover_relevant_files
from ..implementation.context_builder import build_implementation_context
from ..implementation.implementation_planner import build_plan
from ..implementation.suggestion_engine import build_suggestions
from ..implementation.patch_generator import generate_patch
from ..services.verification_runner import VerificationRunner
from ..services.analysis_service import get_or_create_repository
from ..logging_config import get_logger

logger = get_logger(__name__)


class ImplementationService:
    def __init__(self, db: Session, github_client: Optional[GitHubClient] = None, llm_provider=None):
        self.db = db
        self.github = github_client or GitHubClient()
        self.llm = llm_provider or AnthropicProvider()

    # ------------------------------------------------------------------ #
    # repository browsing (GET /repositories/{owner}/{repo}/tree, /files)
    # ------------------------------------------------------------------ #
    def get_repository_tree(self, owner: str, repo: str, ref: Optional[str] = None):
        fetcher = RepositoryFetcher(self.github)
        try:
            return fetcher.load(owner, repo, ref)
        except GitHubError:
            raise

    def get_file_content(self, owner: str, repo: str, path: str, ref: Optional[str] = None) -> str:
        resolved = self.github.resolve_ref(owner, repo, ref)
        content = self.github.get_file_content(owner, repo, path, resolved)
        if content is None:
            raise GitHubError(f"File not found or unreadable: {path} at {ref or 'default branch'}")
        return content

    # ------------------------------------------------------------------ #
    # POST /implementation/analyze
    # ------------------------------------------------------------------ #
    def analyze(
        self, owner: str, repo: str, request_text: str, ref: Optional[str] = None,
        target_file: Optional[str] = None, target_symbol: Optional[str] = None,
        run_verification: bool = False,
    ) -> models.ImplementationRequestRecord:
        repo_record = get_or_create_repository(self.db, owner, repo, self.github)

        fetcher_service = RepositoryFetcher(self.github)
        handle = fetcher_service.load(owner, repo, ref)

        file_fetcher = FileFetcher(self.github, owner, repo, handle.resolved_sha)

        record = models.ImplementationRequestRecord(
            repository_id=repo_record.id,
            ref=handle.ref,
            resolved_sha=handle.resolved_sha,
            request_text=request_text,
            target_file=target_file,
            target_symbol=target_symbol,
            status="PENDING",
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        ctx = ImplementationContext(
            owner=owner, repo=repo, repo_handle=handle, fetcher=file_fetcher,
            request_text=request_text, target_file=target_file, target_symbol=target_symbol,
        )

        try:
            ctx.log("file_discovery", "Scoring repository files for relevance to the request.")
            ctx.relevant_files = discover_relevant_files(
                handle.tree, request_text, file_fetcher, target_file=target_file,
            )
            ctx.log("file_discovery", f"{len(ctx.relevant_files)} relevant file(s) selected.")

            build_implementation_context(ctx)

            agent = ImplementationAgent(self.llm)
            agent.run(ctx)

            plan = build_plan(ctx.plan, tests_to_add=ctx.tests_to_add, agent_risks=ctx.risks)
            suggestions = build_suggestions(ctx.suggestions)
            generated = generate_patch(ctx.patch_text, ctx.files_to_create)

            record.status = "ANALYZED"
            record.summary = ctx.llm_summary
            record.relevant_files = [
                {"path": sf.path, "score": round(sf.score, 2), "reasons": sf.reasons,
                 "is_test": sf.is_test, "is_config": sf.is_config, "matched_symbols": sf.matched_symbols}
                for sf in ctx.relevant_files
            ]
            record.agent_trace = ctx.trace

            plan_record = models.ImplementationPlanRecord(
                request_id=record.id,
                objective=plan.objective,
                assumptions=plan.assumptions,
                files_to_modify=plan.files_to_modify,
                files_to_create=plan.files_to_create,
                symbols_to_modify=plan.symbols_to_modify,
                steps=plan.steps,
                tests=plan.tests,
                risks=plan.risks,
                expected_behavior=plan.expected_behavior,
                raw_patch_text=ctx.patch_text,
                files_to_create_payload=ctx.files_to_create,
            )
            self.db.add(plan_record)

            for s in suggestions:
                self.db.add(models.ImplementationSuggestionRecord(request_id=record.id, **s.to_dict()))

            patch_record = models.GeneratedPatchRecord(
                request_id=record.id,
                patch_text=generated.patch_text,
                is_syntactically_valid=generated.is_syntactically_valid,
                validation_errors=generated.validation_errors,
                files_touched=generated.files_touched,
                new_files_added_deterministically=generated.new_files_added_deterministically,
            )
            self.db.add(patch_record)
            if suggestions:
                record.status = "PATCHED" if generated.patch_text.strip() else "ANALYZED"
            self.db.commit()
            self.db.refresh(record)

            if run_verification and generated.patch_text.strip():
                self.verify(record.id, patch_id=patch_record.id)
                self.db.refresh(record)

        except Exception as e:
            logger.exception(f"implementation_analyze_failed owner={owner} repo={repo}")
            record.status = "ERROR"
            record.error_message = str(e)
            record.agent_trace = ctx.trace
            self.db.commit()
            self.db.refresh(record)
            raise

        return record

    # ------------------------------------------------------------------ #
    # POST /implementation/generate-patch -- reassemble from stored plan
    # output, no new LLM call.
    # ------------------------------------------------------------------ #
    def regenerate_patch(self, implementation_id: int) -> models.GeneratedPatchRecord:
        record = self.db.get(models.ImplementationRequestRecord, implementation_id)
        if not record or not record.plan:
            raise ValueError("Implementation request not found or has no plan yet; run /analyze first.")

        generated = generate_patch(record.plan.raw_patch_text, record.plan.files_to_create_payload)
        patch_record = models.GeneratedPatchRecord(
            request_id=record.id,
            patch_text=generated.patch_text,
            is_syntactically_valid=generated.is_syntactically_valid,
            validation_errors=generated.validation_errors,
            files_touched=generated.files_touched,
            new_files_added_deterministically=generated.new_files_added_deterministically,
        )
        self.db.add(patch_record)
        if generated.patch_text.strip():
            record.status = "PATCHED"
        self.db.commit()
        self.db.refresh(patch_record)
        return patch_record

    # ------------------------------------------------------------------ #
    # POST /implementation/verify
    # ------------------------------------------------------------------ #
    def verify(self, implementation_id: int, patch_id: Optional[int] = None) -> models.ImplementationVerificationRecord:
        record = self.db.get(models.ImplementationRequestRecord, implementation_id)
        if not record:
            raise ValueError("Implementation request not found.")

        patch_record = None
        if patch_id:
            patch_record = self.db.get(models.GeneratedPatchRecord, patch_id)
        elif record.patches:
            patch_record = sorted(record.patches, key=lambda p: p.created_at)[-1]
        if not patch_record:
            raise ValueError("No generated patch to verify. Run /analyze or /generate-patch first.")

        clone_url = self.github.get_clone_url(record.repository.owner, record.repository.name)
        runner = VerificationRunner(clone_url, record.resolved_sha)
        outcome = runner.verify(patch_text=patch_record.patch_text or None)

        verification = models.ImplementationVerificationRecord(
            patch_id=patch_record.id,
            status=outcome.status,
            patch_applied=outcome.patch_applied,
            tests_passed=outcome.tests_passed,
            tests_failed=outcome.tests_failed,
            command_run=outcome.command_run,
            details=outcome.details,
        )
        self.db.add(verification)
        if outcome.status == "VERIFIED":
            record.status = "VERIFIED"
        self.db.commit()
        self.db.refresh(verification)
        return verification
