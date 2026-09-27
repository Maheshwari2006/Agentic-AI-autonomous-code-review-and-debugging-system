from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RepositoryCreate(BaseModel):
    owner: str
    name: str
    branch: Optional[str] = None


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner: str
    name: str
    url: str
    default_branch: str
    created_at: dt.datetime


class CommitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sha: str
    parent_sha: Optional[str]
    message: str
    author: str
    authored_at: Optional[dt.datetime]
    additions: int
    deletions: int
    files_changed: int


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file: str
    function: Optional[str]
    line: Optional[int]
    severity: str
    category: str
    title: str
    description: str
    evidence: str
    impact: str
    suggested_fix_explanation: str
    suggested_patch: str
    confidence: float
    tests_required: str


class VerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    tests_passed: int
    tests_failed: int
    details: str
    command_run: str


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    commit_id: int
    status: str
    summary: str
    confidence: float
    change_groups: list
    agent_trace: list
    created_at: dt.datetime
    issues: list[IssueOut] = []
    verifications: list[VerificationOut] = []


class AnalyzeCommitRequest(BaseModel):
    owner: str
    repo: str
    sha: str
    run_verification: bool = True


class ErrorResponse(BaseModel):
    detail: str
