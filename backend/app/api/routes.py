from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database.session import get_db
from ..database import models
from ..github.client import GitHubClient, GitHubError
from ..services.analysis_service import AnalysisService, get_or_create_repository
from ..schemas import schemas
from ..logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/repositories", response_model=schemas.RepositoryOut)
def create_repository(payload: schemas.RepositoryCreate, db: Session = Depends(get_db)):
    github = GitHubClient()
    try:
        record = get_or_create_repository(db, payload.owner, payload.name, github)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if payload.branch:
        record.default_branch = payload.branch
        db.commit()
        db.refresh(record)
    return record


@router.get("/repositories", response_model=list[schemas.RepositoryOut])
def list_repositories(db: Session = Depends(get_db)):
    return db.query(models.Repository).order_by(models.Repository.created_at.desc()).all()


@router.get("/repositories/{repo_id}", response_model=schemas.RepositoryOut)
def get_repository(repo_id: int, db: Session = Depends(get_db)):
    record = db.get(models.Repository, repo_id)
    if not record:
        raise HTTPException(status_code=404, detail="Repository not found")
    return record


@router.get("/repositories/{repo_id}/commits", response_model=list[schemas.CommitOut])
def list_stored_commits(repo_id: int, db: Session = Depends(get_db)):
    return (
        db.query(models.Commit)
        .filter(models.Commit.repository_id == repo_id)
        .order_by(models.Commit.created_at.desc())
        .all()
    )


@router.get("/repositories/{repo_id}/github-commits")
def list_github_commits(repo_id: int, branch: str | None = None, per_page: int = 30, db: Session = Depends(get_db)):
    """List commits directly from GitHub (not yet necessarily analyzed)."""
    record = db.get(models.Repository, repo_id)
    if not record:
        raise HTTPException(status_code=404, detail="Repository not found")
    github = GitHubClient()
    try:
        commits = github.list_commits(record.owner, record.name, branch=branch or record.default_branch, per_page=per_page)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [
        {
            "sha": c["sha"],
            "message": c.get("commit", {}).get("message", "").split("\n")[0],
            "author": c.get("commit", {}).get("author", {}).get("name", ""),
            "date": c.get("commit", {}).get("author", {}).get("date", ""),
            "html_url": c.get("html_url", ""),
        }
        for c in commits
    ]


@router.get("/commits/{sha}")
def get_commit_by_sha(sha: str, owner: str, repo: str, db: Session = Depends(get_db)):
    """Fetch raw commit metadata from GitHub (does not require a prior analysis)."""
    github = GitHubClient()
    try:
        detail = github.get_commit(owner, repo, sha)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "sha": detail.sha,
        "message": detail.message,
        "author": detail.author_name,
        "authored_at": detail.authored_at,
        "parent_sha": detail.parent_sha,
        "additions": detail.additions,
        "deletions": detail.deletions,
        "files": [
            {"filename": f.filename, "status": f.status, "additions": f.additions, "deletions": f.deletions}
            for f in detail.files
        ],
    }


@router.post("/commits/{sha}/analyze", response_model=schemas.ReviewOut)
def analyze_commit(sha: str, payload: schemas.AnalyzeCommitRequest, db: Session = Depends(get_db)):
    if payload.sha != sha:
        raise HTTPException(status_code=400, detail="sha in path and body must match")
    service = AnalysisService(db, run_verification=payload.run_verification)
    try:
        review = service.analyze_commit(payload.owner, payload.repo, sha)
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # e.g. missing ANTHROPIC_API_KEY, malformed LLM response chain, etc.
        raise HTTPException(status_code=502, detail=str(e))
    return review


@router.get("/reviews/{review_id}", response_model=schemas.ReviewOut)
def get_review(review_id: int, db: Session = Depends(get_db)):
    review = db.get(models.Review, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.get("/reviews/{review_id}/issues", response_model=list[schemas.IssueOut])
def get_review_issues(review_id: int, db: Session = Depends(get_db)):
    review = db.get(models.Review, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review.issues


@router.get("/commits/{commit_id}/reviews", response_model=list[schemas.ReviewOut])
def get_commit_reviews(commit_id: int, db: Session = Depends(get_db)):
    return db.query(models.Review).filter(models.Review.commit_id == commit_id).order_by(models.Review.created_at.desc()).all()
