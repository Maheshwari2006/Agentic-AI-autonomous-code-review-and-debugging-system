from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TreeEntryOut(BaseModel):
    path: str
    type: str  # "file" | "directory"
    is_test: bool = False
    is_config: bool = False
    size: int = 0


class RepositoryTreeOut(BaseModel):
    owner: str
    repo: str
    ref: str
    resolved_sha: str
    default_branch: str
    file_count: int
    directory_count: int
    entries: list[TreeEntryOut]


class RepositoryFileOut(BaseModel):
    owner: str
    repo: str
    ref: str
    path: str
    content: str
    language: str


class RelevantFileOut(BaseModel):
    path: str
    score: float
    reasons: list[str] = []
    is_test: bool = False
    is_config: bool = False
    matched_symbols: list[str] = []
