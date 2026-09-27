from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patch_text: str
    is_syntactically_valid: bool
    validation_errors: list[str] = []
    files_touched: list[str] = []
    new_files_added_deterministically: list[str] = []
    created_at: dt.datetime


class ImplementationVerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    patch_applied: bool
    tests_passed: int
    tests_failed: int
    command_run: str
    details: str
    created_at: dt.datetime


class GeneratePatchRequest(BaseModel):
    implementation_id: int


class VerifyPatchRequest(BaseModel):
    implementation_id: int
    patch_id: Optional[int] = None  # defaults to the request's most recent patch
