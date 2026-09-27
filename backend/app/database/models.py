"""
SQLAlchemy ORM models.

SQLite is used for the prototype (see config.DATABASE_URL) but every model
here uses only portable column types / constructs, so switching
DATABASE_URL to a PostgreSQL DSN (postgresql+psycopg://...) requires no
model changes -- just `pip install psycopg[binary]` and updating .env.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    ForeignKey,
    DateTime,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True)
    owner = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=False)
    default_branch = Column(String(255), default="main")
    created_at = Column(DateTime, default=utcnow)

    commits = relationship("Commit", back_populates="repository", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("owner", "name", name="uq_repo_owner_name"),)


class Commit(Base):
    __tablename__ = "commits"

    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    sha = Column(String(64), nullable=False, index=True)
    parent_sha = Column(String(64), nullable=True)
    message = Column(Text, default="")
    author = Column(String(255), default="")
    author_email = Column(String(255), default="")
    authored_at = Column(DateTime, nullable=True)
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    files_changed = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    repository = relationship("Repository", back_populates="commits")
    changed_files = relationship("ChangedFile", back_populates="commit", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="commit", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_commit_repo_sha"),)


class ChangedFile(Base):
    __tablename__ = "changed_files"

    id = Column(Integer, primary_key=True)
    commit_id = Column(Integer, ForeignKey("commits.id"), nullable=False)
    path = Column(String(1024), nullable=False)
    status = Column(String(32), default="modified")  # added|modified|removed|renamed
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    previous_content = Column(Text, nullable=True)
    current_content = Column(Text, nullable=True)
    patch = Column(Text, nullable=True)
    language = Column(String(64), nullable=True)

    commit = relationship("Commit", back_populates="changed_files")
    symbols = relationship("ChangedSymbol", back_populates="changed_file", cascade="all, delete-orphan")


class ChangedSymbol(Base):
    __tablename__ = "changed_symbols"

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey("changed_files.id"), nullable=False)
    symbol_name = Column(String(512), nullable=False)
    qualified_name = Column(String(1024), nullable=True)
    symbol_type = Column(String(32), default="function")  # function|method|class
    change_type = Column(String(32), default="modified")  # added|modified|removed
    start_line = Column(Integer, nullable=True)
    end_line = Column(Integer, nullable=True)
    previous_source = Column(Text, nullable=True)
    new_source = Column(Text, nullable=True)
    change_group = Column(String(64), nullable=True)  # logical grouping label

    changed_file = relationship("ChangedFile", back_populates="symbols")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    commit_id = Column(Integer, ForeignKey("commits.id"), nullable=False)
    status = Column(String(32), default="PENDING")  # PENDING|APPROVED|NEEDS_CHANGES|ERROR
    summary = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    change_groups = Column(JSON, default=list)
    agent_trace = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)

    commit = relationship("Commit", back_populates="reviews")
    issues = relationship("Issue", back_populates="review", cascade="all, delete-orphan")
    verifications = relationship("Verification", back_populates="review", cascade="all, delete-orphan")


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False)
    file = Column(String(1024), nullable=False)
    function = Column(String(512), nullable=True)
    line = Column(Integer, nullable=True)
    severity = Column(String(16), default="MEDIUM")  # CRITICAL|HIGH|MEDIUM|LOW|INFO
    category = Column(String(32), default="CORRECTNESS")
    title = Column(String(512), default="")
    description = Column(Text, default="")
    evidence = Column(Text, default="")
    impact = Column(Text, default="")
    suggested_fix_explanation = Column(Text, default="")
    suggested_patch = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    tests_required = Column(Text, default="")

    review = relationship("Review", back_populates="issues")


class Verification(Base):
    __tablename__ = "verifications"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False)
    status = Column(String(32), default="UNABLE_TO_VERIFY")
    tests_passed = Column(Integer, default=0)
    tests_failed = Column(Integer, default=0)
    details = Column(Text, default="")
    command_run = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)

    review = relationship("Review", back_populates="verifications")


# --------------------------------------------------------------------- #
# AI Repository Code Fetching + Implementation Suggestion feature.
# Kept as separate tables from Review/Issue/Verification above (which
# model the existing *commit-diff* review pipeline) since an
# ImplementationRequest analyzes a whole repository at a ref for a
# free-text feature request, not one specific commit's changes -- but it
# links back to the same Repository table rather than duplicating
# owner/name/url on every row.
# --------------------------------------------------------------------- #


class ImplementationRequestRecord(Base):
    __tablename__ = "implementation_requests"

    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    ref = Column(String(255), nullable=False)  # branch/tag/sha as the caller specified it
    resolved_sha = Column(String(64), nullable=False)
    request_text = Column(Text, nullable=False)
    target_file = Column(String(1024), nullable=True)
    target_symbol = Column(String(512), nullable=True)
    status = Column(String(32), default="PENDING")  # PENDING|ANALYZED|PATCHED|VERIFIED|ERROR
    summary = Column(Text, default="")
    relevant_files = Column(JSON, default=list)  # [{"path","score","reasons":[...]}]
    agent_trace = Column(JSON, default=list)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)

    repository = relationship("Repository")
    plan = relationship(
        "ImplementationPlanRecord", back_populates="request",
        uselist=False, cascade="all, delete-orphan",
    )
    suggestions = relationship(
        "ImplementationSuggestionRecord", back_populates="request", cascade="all, delete-orphan",
    )
    patches = relationship(
        "GeneratedPatchRecord", back_populates="request", cascade="all, delete-orphan",
    )


class ImplementationPlanRecord(Base):
    __tablename__ = "implementation_plans"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("implementation_requests.id"), nullable=False, unique=True)
    objective = Column(Text, default="")
    assumptions = Column(JSON, default=list)
    files_to_modify = Column(JSON, default=list)
    files_to_create = Column(JSON, default=list)
    symbols_to_modify = Column(JSON, default=list)
    steps = Column(JSON, default=list)
    tests = Column(JSON, default=list)
    risks = Column(JSON, default=list)
    expected_behavior = Column(Text, default="")
    # Raw agent output retained so /implementation/generate-patch can
    # reassemble a patch on demand without a new LLM call.
    raw_patch_text = Column(Text, default="")
    files_to_create_payload = Column(JSON, default=list)  # [{"path","content","reason"}]
    created_at = Column(DateTime, default=utcnow)

    request = relationship("ImplementationRequestRecord", back_populates="plan")


class ImplementationSuggestionRecord(Base):
    __tablename__ = "implementation_suggestions"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("implementation_requests.id"), nullable=False)
    file = Column(String(1024), nullable=False)
    symbol = Column(String(512), default="")
    location = Column(Text, default="")
    change_type = Column(String(16), default="recommended")  # required|recommended|optional
    current_behavior = Column(Text, default="")
    proposed_change = Column(Text, default="")
    reason = Column(Text, default="")
    expected_behavior = Column(Text, default="")

    request = relationship("ImplementationRequestRecord", back_populates="suggestions")


class GeneratedPatchRecord(Base):
    __tablename__ = "generated_patches"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("implementation_requests.id"), nullable=False)
    patch_text = Column(Text, default="")
    is_syntactically_valid = Column(Boolean, default=False)
    validation_errors = Column(JSON, default=list)
    files_touched = Column(JSON, default=list)
    new_files_added_deterministically = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)

    request = relationship("ImplementationRequestRecord", back_populates="patches")
    verification = relationship(
        "ImplementationVerificationRecord", back_populates="patch",
        uselist=False, cascade="all, delete-orphan",
    )


class ImplementationVerificationRecord(Base):
    __tablename__ = "implementation_verifications"

    id = Column(Integer, primary_key=True)
    patch_id = Column(Integer, ForeignKey("generated_patches.id"), nullable=False, unique=True)
    status = Column(String(32), default="UNABLE_TO_VERIFY")  # VERIFIED|PARTIALLY_VERIFIED|FAILED|UNABLE_TO_VERIFY
    patch_applied = Column(Boolean, default=False)
    tests_passed = Column(Integer, default=0)
    tests_failed = Column(Integer, default=0)
    command_run = Column(Text, default="")
    details = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)

    patch = relationship("GeneratedPatchRecord", back_populates="verification")
