"""
API-level tests using FastAPI's TestClient with the database pointed at
an in-memory SQLite instance and GitHub/Anthropic calls mocked out.

Requires `fastapi`, `httpx`, and `sqlalchemy` to be installed -- these are
listed in requirements.txt. This sandbox's analysis environment had no
package-index network access, so this file is verified by static
inspection + `py_compile` here; run it for real with:

    pip install -r requirements.txt
    pytest backend/tests/test_api.py
"""
import os
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@patch("app.services.analysis_service.GitHubClient")
def test_create_repository(mock_github_cls, client):
    mock_github = MagicMock()
    mock_github.get_repository.return_value = {
        "html_url": "https://github.com/octocat/hello",
        "default_branch": "main",
    }
    mock_github_cls.return_value = mock_github

    with patch("app.api.routes.GitHubClient", return_value=mock_github):
        resp = client.post("/api/repositories", json={"owner": "octocat", "name": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner"] == "octocat"
    assert data["name"] == "hello"


def test_get_nonexistent_review_returns_404(client):
    resp = client.get("/api/reviews/999")
    assert resp.status_code == 404
