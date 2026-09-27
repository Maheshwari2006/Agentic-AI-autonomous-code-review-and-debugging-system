from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from ..config import get_settings
from .models import Base


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # sqlite:///./data/agentic_review.db -> ensure the data/ dir exists
        path_part = url.split("sqlite:///")[-1]
        if path_part and path_part != ":memory:":
            os.makedirs(os.path.dirname(path_part) or ".", exist_ok=True)
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
