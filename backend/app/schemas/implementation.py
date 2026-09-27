from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .code_context import RelevantFileOut
from .patch import ImplementationVerificationOut, PatchOut


class ImplementationAnalyzeRequest(BaseModel):
    owner: str
    repo: str
    ref: Optional[str] = Field(default=None, description="Branch, tag, or commit SHA. Defaults to the default branch.")
    request: str = Field(min_length=3, description="Natural-language description of the feature/change to implement.")
    target_file: Optional[str] = None
    target_symbol: Optional[str] = None
    run_verification: bool = False


class ImplementationPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    objective: str
    assumptions: list[str] = []
    files_to_modify: list[str] = []
    files_to_create: list[str] = []
    symbols_to_modify: list[str] = []
    steps: list[str] = []
    tests: list[str] = []
    risks: list[str] = []
    expected_behavior: str = ""


class ImplementationSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file: str
    symbol: str
    location: str
    change_type: str
    current_behavior: str
    proposed_change: str
    reason: str
    expected_behavior: str


class ImplementationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    repository_id: int
    ref: str
    resolved_sha: str
    request_text: str
    target_file: Optional[str]
    target_symbol: Optional[str]
    status: str
    summary: str
    relevant_files: list[dict] = []
    agent_trace: list[dict] = []
    error_message: str = ""
    created_at: dt.datetime


class ImplementationFullOut(ImplementationRequestOut):
    """The complete picture for GET /implementation/{id}: request + plan +
    suggestions + latest patch + latest verification, all in one payload
    so the frontend doesn't need four round trips."""
    plan: Optional[ImplementationPlanOut] = None
    suggestions: list[ImplementationSuggestionOut] = []
    latest_patch: Optional[PatchOut] = None
    latest_verification: Optional[ImplementationVerificationOut] = None
