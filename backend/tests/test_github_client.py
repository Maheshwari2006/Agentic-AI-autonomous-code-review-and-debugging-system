from unittest.mock import MagicMock, patch

import pytest

from app.github.client import GitHubClient, GitHubError


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.headers = {}
    return resp


@patch("app.github.client.requests.Session.request")
def test_get_commit_parses_metadata_and_files(mock_request):
    mock_request.return_value = _mock_response(
        200,
        {
            "sha": "abc123",
            "html_url": "https://github.com/o/r/commit/abc123",
            "parents": [{"sha": "parent123"}],
            "commit": {
                "message": "Fix bug",
                "author": {"name": "Alice", "email": "a@example.com", "date": "2026-01-01T00:00:00Z"},
            },
            "stats": {"additions": 5, "deletions": 2},
            "files": [
                {
                    "filename": "auth.py",
                    "status": "modified",
                    "additions": 5,
                    "deletions": 2,
                    "patch": "@@ -1,2 +1,3 @@\n+added line",
                }
            ],
        },
    )
    client = GitHubClient(token="", base_url="https://api.github.com")
    detail = client.get_commit("o", "r", "abc123")

    assert detail.sha == "abc123"
    assert detail.parent_sha == "parent123"
    assert detail.message == "Fix bug"
    assert detail.author_name == "Alice"
    assert len(detail.files) == 1
    assert detail.files[0].filename == "auth.py"
    assert detail.files[0].patch.startswith("@@")


@patch("app.github.client.requests.Session.request")
def test_rate_limit_raises_friendly_error(mock_request):
    mock_request.return_value = _mock_response(403, {}, text="API rate limit exceeded")
    client = GitHubClient(token="")
    with pytest.raises(GitHubError, match="rate limit"):
        client.get_repository("o", "r")


@patch("app.github.client.requests.Session.request")
def test_not_found_raises_friendly_error(mock_request):
    mock_request.return_value = _mock_response(404, {}, text="Not Found")
    client = GitHubClient(token="")
    with pytest.raises(GitHubError, match="not found"):
        client.get_repository("o", "missing-repo")


@patch("app.github.client.requests.Session.request")
def test_get_file_content_decodes_base64(mock_request):
    import base64

    encoded = base64.b64encode(b"print('hello')").decode()
    mock_request.return_value = _mock_response(200, {"encoding": "base64", "content": encoded})
    client = GitHubClient(token="")
    content = client.get_file_content("o", "r", "hello.py", "abc123")
    assert content == "print('hello')"


@patch("app.github.client.requests.Session.request")
def test_get_file_content_returns_none_for_directory(mock_request):
    mock_request.return_value = _mock_response(200, [{"name": "file1.py"}, {"name": "file2.py"}])
    client = GitHubClient(token="")
    content = client.get_file_content("o", "r", "somedir", "abc123")
    assert content is None


def test_clone_url_includes_token_when_present():
    client = GitHubClient(token="ghp_secrettoken")
    url = client.get_clone_url("o", "r")
    assert "ghp_secrettoken" in url
    assert url.startswith("https://ghp_secrettoken@")


def test_clone_url_omits_token_when_absent():
    client = GitHubClient(token="")
    url = client.get_clone_url("o", "r")
    assert url == "https://github.com/o/r.git"
