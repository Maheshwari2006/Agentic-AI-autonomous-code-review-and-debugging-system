"""Agent 7 -- Verification Agent.

For every issue that got a suggested_patch from the Fix Generator, runs
the real verification workflow (clone -> checkout commit -> apply patch
-> run tests) via services/verification_runner.py and records the
outcome. Never claims a fix works without actually having run something;
if verification genuinely cannot run (no test suite, sandboxed
environment with no outbound network, etc.) the status is
UNABLE_TO_VERIFY, not VERIFIED.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from ..github.client import GitHubClient
from ..services.verification_runner import VerificationRunner
from ..logging_config import get_logger

logger = get_logger(__name__)


class VerificationAgent(Agent):
    name = "verification_agent"

    def __init__(self, github_client: GitHubClient):
        self.github = github_client

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        clone_url = self.github.get_clone_url(ctx.owner, ctx.repo)
        checked = 0

        for issue in ctx.issues:
            patch = issue.get("suggested_patch")
            if not patch:
                continue
            checked += 1
            runner = VerificationRunner(clone_url, ctx.commit.sha)
            outcome = runner.verify(patch_text=patch)
            issue["verification_status"] = outcome.status
            issue["verification_details"] = outcome.details
            ctx.verification_results.append(
                {
                    "issue_title": issue.get("title"),
                    "status": outcome.status,
                    "tests_passed": outcome.tests_passed,
                    "tests_failed": outcome.tests_failed,
                    "command_run": outcome.command_run,
                    "details": outcome.details,
                }
            )
            ctx.log(self.name, f"Verified patch for '{issue.get('title')}': {outcome.status}")

        if checked == 0:
            ctx.log(self.name, "No patches with sufficient confidence to verify.")
        else:
            ctx.log(self.name, f"Verification complete for {checked} patch(es).")
        return ctx
