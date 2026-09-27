"""
AgentPipeline -- runs all 8 agents in order against one AnalysisContext.

Each stage is wrapped so a failure in one agent (e.g. the LLM being
unreachable during the review stage) is recorded in the trace and turns
the overall review status into ERROR rather than crashing the whole
request -- earlier stages' work (repository exploration, change
detection) is still persisted and useful on its own.
"""
from __future__ import annotations

from .base import AnalysisContext
from .repository_agent import RepositoryExplorerAgent
from .change_agent import ChangeAnalyzerAgent
from .dependency_agent import DependencyAnalyzerAgent
from .review_agent import ReviewAgent
from .debugging_agent import DebuggingAgent
from .fix_agent import FixAgent
from .verification_agent import VerificationAgent
from .report_agent import ReportAgent
from ..github.client import GitHubClient
from ..llm.provider import LLMProvider
from ..logging_config import get_logger

logger = get_logger(__name__)


class AgentPipeline:
    def __init__(self, github_client: GitHubClient, llm: LLMProvider, run_verification: bool = True):
        self.stages = [
            RepositoryExplorerAgent(github_client),
            ChangeAnalyzerAgent(),
            DependencyAnalyzerAgent(github_client),
            ReviewAgent(llm),
            DebuggingAgent(llm),
            FixAgent(llm),
        ]
        if run_verification:
            self.stages.append(VerificationAgent(github_client))
        self.stages.append(ReportAgent())

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        for stage in self.stages:
            try:
                ctx = stage.run(ctx)
            except Exception as e:
                logger.exception(f"agent_stage_failed stage={stage.name}")
                ctx.log(stage.name, f"Stage failed: {e}", level="error")
                ctx.review_status = "ERROR"
                ctx.review_summary = f"Analysis stopped: '{stage.name}' failed: {e}"
                break
        return ctx
