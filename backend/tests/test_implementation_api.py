"""
API-level tests for the implementation feature (POST /api/implementation/*
and GET /api/repositories/{owner}/{repo}/tree|files). Follows the same
mocking approach as tests/test_api.py: TestClient against an in-memory
SQLite DB, with GitHubClient and the LLM provider mocked out so no real
network calls happen.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test_impl.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from app.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def _mock_github():
    github = MagicMock()
    github.get_repository.return_value = {"html_url": "https://github.com/octocat/hello", "default_branch": "main"}
    github.resolve_ref.return_value = "f" * 40
    github.get_tree.return_value = [
        {"path": "backend", "type": "tree"},
        {"path": "backend/app.py", "type": "blob", "sha": "s1", "size": 200},
        {"path": "backend/auth.py", "type": "blob", "sha": "s2", "size": 150},
    ]
    github.get_file_content.side_effect = lambda owner, repo, path, ref: {
        "backend/app.py": "def get_user():\n    return {}\n",
        "backend/auth.py": "def encode_jwt(p):\n    return p\n",
    }.get(path)
    github.get_clone_url.return_value = "https://github.com/octocat/hello.git"
    return github


_VALID_AGENT_RESPONSE = {
    "summary": "Add JWT auth.",
    "implementation_plan": {
        "objective": "Add JWT auth", "assumptions": [], "files_to_modify": ["backend/app.py"],
        "files_to_create": [], "symbols_to_modify": [], "steps": ["do it"], "risks": [],
        "expected_behavior": "401 without token",
    },
    "suggestions": [{
        "file": "backend/app.py", "symbol": "get_user", "location": "inside get_user",
        "change_type": "required", "current_behavior": "no auth", "proposed_change": "add require_auth()",
        "reason": "endpoint is unauthenticated", "expected_behavior": "401 without token",
    }],
    "files_to_create": [],
    "patch": "--- a/backend/app.py\n+++ b/backend/app.py\n@@ -1,2 +1,3 @@\n def get_user():\n+    require_auth()\n     return {}\n",
    "tests_to_add": ["test 401"],
    "verification_requirements": ["run pytest"],
    "risks": [],
}


def _mock_llm():
    from app.llm.provider import LLMResponse

    llm = MagicMock()
    llm.complete.return_value = LLMResponse(text=json.dumps(_VALID_AGENT_RESPONSE), tool_calls=[], stop_reason="end_turn")
    return llm


@patch("app.services.implementation_service.GitHubClient")
def test_get_repository_tree(mock_github_cls, client):
    mock_github_cls.return_value = _mock_github()
    resp = client.get("/api/repositories/octocat/hello/tree")
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_count"] == 2
    assert any(e["path"] == "backend/app.py" for e in data["entries"])


@patch("app.services.implementation_service.GitHubClient")
def test_get_repository_file(mock_github_cls, client):
    mock_github_cls.return_value = _mock_github()
    resp = client.get("/api/repositories/octocat/hello/files/backend/app.py")
    assert resp.status_code == 200
    assert "get_user" in resp.json()["content"]


@patch("app.services.implementation_service.AnthropicProvider")
@patch("app.services.implementation_service.GitHubClient")
def test_analyze_implementation_end_to_end(mock_github_cls, mock_llm_cls, client):
    mock_github_cls.return_value = _mock_github()
    mock_llm_cls.return_value = _mock_llm()

    resp = client.post("/api/implementation/analyze", json={
        "owner": "octocat", "repo": "hello", "request": "Add JWT authentication to the user API",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ANALYZED", "PATCHED")
    assert data["plan"]["objective"] == "Add JWT auth"
    assert len(data["suggestions"]) == 1
    assert data["latest_patch"]["is_syntactically_valid"] is True
    assert "require_auth" in data["latest_patch"]["patch_text"]

    # GET the full record back
    get_resp = client.get(f"/api/implementation/{data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == data["id"]


@patch("app.services.implementation_service.AnthropicProvider")
@patch("app.services.implementation_service.GitHubClient")
def test_generate_patch_endpoint_regenerates_from_stored_plan(mock_github_cls, mock_llm_cls, client):
    mock_github_cls.return_value = _mock_github()
    mock_llm_cls.return_value = _mock_llm()

    analyze_resp = client.post("/api/implementation/analyze", json={
        "owner": "octocat", "repo": "hello", "request": "Add JWT authentication",
    })
    implementation_id = analyze_resp.json()["id"]

    resp = client.post("/api/implementation/generate-patch", json={"implementation_id": implementation_id})
    assert resp.status_code == 200
    assert resp.json()["is_syntactically_valid"] is True


def test_get_nonexistent_implementation_returns_404(client):
    resp = client.get("/api/implementation/999999")
    assert resp.status_code == 404


def test_verify_without_patch_returns_404(client):
    resp = client.post("/api/implementation/verify", json={"implementation_id": 999999})
    assert resp.status_code == 404
