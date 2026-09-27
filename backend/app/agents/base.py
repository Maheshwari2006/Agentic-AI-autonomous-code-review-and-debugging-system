"""
Shared pipeline state.

All eight agents operate on one `AnalysisContext` instance, appending to
it as they go, rather than each agent independently re-fetching/re-
sending large amounts of code to the LLM. This is the "share a common
state/context object" design called for in the spec.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from ..github.client import CommitDetail
from ..analysis.dependency import SymbolIndex


@dataclass
class AnalysisContext:
    owner: str
    repo: str
    commit: CommitDetail
    index: SymbolIndex = field(default_factory=SymbolIndex)
    changed_symbols: list = field(default_factory=list)  # list[ChangedSymbolInfo]
    change_groups: list = field(default_factory=list)  # list[list[ChangedSymbolInfo]]
    extra_file_contents: dict = field(default_factory=dict)  # path -> content, for tool reads
    trace: list = field(default_factory=list)  # agent progress log
    issues: list = field(default_factory=list)  # accumulated Issue dicts
    review_summary: str = ""
    review_status: str = "PENDING"
    review_confidence: float = 0.0
    verification_results: list = field(default_factory=list)

    def log(self, agent: str, message: str, **extra) -> None:
        self.trace.append(
            {
                "agent": agent,
                "message": message,
                "timestamp": dt.datetime.utcnow().isoformat() + "Z",
                **extra,
            }
        )


class Agent:
    """Base class -- mostly here to standardize the `.run(ctx)` signature
    and give every agent a name for the trace log / progress UI."""

    name = "agent"

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        raise NotImplementedError
