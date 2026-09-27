"""
ImplementationContext -- shared pipeline state for the AI code
fetching + implementation suggestion feature, mirroring how
agents/base.py's AnalysisContext works for the existing commit-review
pipeline: every stage (file discovery, context building, the
implementation agent, planning, suggestion generation, patch
generation, verification) reads from and appends to one instance
instead of re-fetching/re-sending things independently.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from ..github.file_fetcher import FileFetcher
from ..github.repository_fetcher import RepositoryHandle
from ..analysis.dependency import SymbolIndex


@dataclass
class ImplementationContext:
    owner: str
    repo: str
    repo_handle: RepositoryHandle
    fetcher: FileFetcher
    request_text: str
    target_file: Optional[str] = None
    target_symbol: Optional[str] = None

    index: SymbolIndex = field(default_factory=SymbolIndex)
    relevant_files: list = field(default_factory=list)  # list[ScoredFile]
    context_bundle: dict = field(default_factory=dict)  # built by context_builder

    plan: Optional[dict] = None  # implementation_planner output
    suggestions: list = field(default_factory=list)  # suggestion_engine output
    patch_text: str = ""
    files_to_create: list = field(default_factory=list)  # list[{"path","content"}]
    tests_to_add: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    llm_summary: str = ""

    trace: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        return self.repo_handle.resolved_sha

    def log(self, stage: str, message: str, **extra) -> None:
        self.trace.append({
            "stage": stage,
            "message": message,
            "timestamp": dt.datetime.utcnow().isoformat() + "Z",
            **extra,
        })
