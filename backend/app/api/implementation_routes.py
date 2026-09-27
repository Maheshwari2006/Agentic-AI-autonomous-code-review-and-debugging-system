from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database.session import get_db
from ..database import models
from ..github.client import GitHubError
from ..services.implementation_service import ImplementationService
from ..schemas.implementation import (
    ImplementationAnalyzeRequest,
    ImplementationFullOut,
    ImplementationRequestOut,
)
from ..schemas.code_context import RepositoryTreeOut, RepositoryFileOut, TreeEntryOut
from ..schemas.patch import GeneratePatchRequest, PatchOut, VerifyPatchRequest, ImplementationVerificationOut
from ..logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------- #
# repository browsing
# ---------------------------------------------------------------------- #
@router.get("/repositories/{owner}/{repo}/tree", response_model=RepositoryTreeOut)
def get_repository_tree(owner: str, repo: str, ref: str | None = None, db: Session = Depends(get_db)):
    service = ImplementationService(db)
    try:
        handle = service.get_repository_tree(owner, repo, ref)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entries = [
        TreeEntryOut(path=f.path, type="file", is_test=f.is_test, is_config=f.is_config, size=f.size)
        for f in handle.tree.files
    ] + [TreeEntryOut(path=d, type="directory") for d in handle.tree.directories]
    return RepositoryTreeOut(
        owner=owner, repo=repo, ref=handle.ref, resolved_sha=handle.resolved_sha,
        default_branch=handle.default_branch,
        file_count=len(handle.tree.files), directory_count=len(handle.tree.directories),
        entries=entries,
    )


@router.get("/repositories/{owner}/{repo}/files", response_model=list[TreeEntryOut])
def list_repository_files(owner: str, repo: str, ref: str | None = None, db: Session = Depends(get_db)):
    """Flat list of source files (directories excluded) in the repository
    tree at `ref`. For targeted retrieval instead of a full listing, use
    POST /implementation/analyze, which scores relevance automatically."""
    service = ImplementationService(db)
    try:
        handle = service.get_repository_tree(owner, repo, ref)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [
        TreeEntryOut(path=f.path, type="file", is_test=f.is_test, is_config=f.is_config, size=f.size)
        for f in handle.tree.files
    ]


@router.get("/repositories/{owner}/{repo}/files/{path:path}", response_model=RepositoryFileOut)
def get_repository_file(owner: str, repo: str, path: str, ref: str | None = None, db: Session = Depends(get_db)):
    from ..parsing.base import detect_language

    service = ImplementationService(db)
    try:
        content = service.get_file_content(owner, repo, path, ref)
        resolved = service.github.resolve_ref(owner, repo, ref)
    except GitHubError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return RepositoryFileOut(
        owner=owner, repo=repo, ref=ref or resolved, path=path, content=content,
        language=detect_language(path),
    )


# ---------------------------------------------------------------------- #
# implementation pipeline
# ---------------------------------------------------------------------- #
def _to_full_out(record: models.ImplementationRequestRecord) -> ImplementationFullOut:
    latest_patch = sorted(record.patches, key=lambda p: p.created_at)[-1] if record.patches else None
    latest_verification = None
    if latest_patch and latest_patch.verification:
        latest_verification = latest_patch.verification

    return ImplementationFullOut(
        id=record.id,
        repository_id=record.repository_id,
        ref=record.ref,
        resolved_sha=record.resolved_sha,
        request_text=record.request_text,
        target_file=record.target_file,
        target_symbol=record.target_symbol,
        status=record.status,
        summary=record.summary,
        relevant_files=record.relevant_files or [],
        agent_trace=record.agent_trace or [],
        error_message=record.error_message or "",
        created_at=record.created_at,
        plan=record.plan,
        suggestions=record.suggestions,
        latest_patch=latest_patch,
        latest_verification=latest_verification,
    )


@router.post("/implementation/analyze", response_model=ImplementationFullOut)
def analyze_implementation(payload: ImplementationAnalyzeRequest, db: Session = Depends(get_db)):
    service = ImplementationService(db)
    try:
        record = service.analyze(
            owner=payload.owner, repo=payload.repo, request_text=payload.request,
            ref=payload.ref, target_file=payload.target_file, target_symbol=payload.target_symbol,
            run_verification=payload.run_verification,
        )
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # e.g. missing ANTHROPIC_API_KEY, or the agent could not produce a
        # valid structured response after its tool-call budget.
        raise HTTPException(status_code=502, detail=str(e))
    return _to_full_out(record)


@router.post("/implementation/suggest", response_model=ImplementationFullOut)
def get_implementation_suggestions(payload: GeneratePatchRequest, db: Session = Depends(get_db)):
    """Returns the suggestions already produced by /analyze for this
    implementation request. Suggestions are generated as part of the
    agent's single structured analysis call (see agents/implementation_agent.py)
    rather than a second independent LLM call, so this endpoint is a
    focused view onto that result rather than a new analysis."""
    record = db.get(models.ImplementationRequestRecord, payload.implementation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Implementation request not found")
    if record.status == "PENDING":
        raise HTTPException(status_code=409, detail="Analysis has not completed yet.")
    return _to_full_out(record)


@router.post("/implementation/generate-patch", response_model=PatchOut)
def generate_implementation_patch(payload: GeneratePatchRequest, db: Session = Depends(get_db)):
    service = ImplementationService(db)
    try:
        patch_record = service.regenerate_patch(payload.implementation_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return patch_record


@router.post("/implementation/verify", response_model=ImplementationVerificationOut)
def verify_implementation_patch(payload: VerifyPatchRequest, db: Session = Depends(get_db)):
    service = ImplementationService(db)
    try:
        verification = service.verify(payload.implementation_id, patch_id=payload.patch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return verification


@router.get("/implementation/{implementation_id}", response_model=ImplementationFullOut)
def get_implementation(implementation_id: int, db: Session = Depends(get_db)):
    record = db.get(models.ImplementationRequestRecord, implementation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Implementation request not found")
    return _to_full_out(record)


@router.get("/implementation", response_model=list[ImplementationRequestOut])
def list_implementations(repository_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(models.ImplementationRequestRecord)
    if repository_id:
        q = q.filter(models.ImplementationRequestRecord.repository_id == repository_id)
    return q.order_by(models.ImplementationRequestRecord.created_at.desc()).all()
