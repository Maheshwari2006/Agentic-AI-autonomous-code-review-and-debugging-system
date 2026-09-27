from app.implementation.context import ImplementationContext
from app.implementation.context_builder import build_implementation_context
from app.implementation.file_discovery import ScoredFile
from app.github.repository_fetcher import RepositoryHandle
from app.github.tree_parser import ParsedTree


class _StubFetcher:
    def __init__(self, contents: dict):
        self._contents = contents

    def get(self, path):
        return self._contents.get(path)

    def get_many(self, paths):
        return {p: self._contents[p] for p in paths if p in self._contents}


def _handle():
    return RepositoryHandle(
        owner="o", repo="r", ref="main", resolved_sha="a" * 40,
        default_branch="main", html_url="https://github.com/o/r",
        tree=ParsedTree(files=[], directories=[]),
    )


def test_context_bundle_includes_files_and_symbols():
    contents = {
        "auth.py": "def login(u, p):\n    return validate_token(p)\n\ndef validate_token(t):\n    return len(t) > 0\n",
        "routes.py": "from auth import login\n\ndef handle_request(req):\n    return login(req.user, req.pw)\n",
    }
    fetcher = _StubFetcher(contents)
    ctx = ImplementationContext(
        owner="o", repo="r", repo_handle=_handle(), fetcher=fetcher,
        request_text="handle login requests",
    )
    ctx.relevant_files = [
        ScoredFile(path="auth.py", score=10.0, reasons=["match"], matched_symbols=["login"]),
        ScoredFile(path="routes.py", score=5.0, reasons=["match"]),
    ]
    bundle = build_implementation_context(ctx)

    paths = {f["path"] for f in bundle["files"]}
    assert paths == {"auth.py", "routes.py"}
    auth_file = next(f for f in bundle["files"] if f["path"] == "auth.py")
    assert any(s["name"] == "login" for s in auth_file["symbols"])


def test_context_bundle_pulls_in_dependency_context_for_matched_symbols():
    contents = {
        "auth.py": "def login(u, p):\n    return validate_token(p)\n\ndef validate_token(t):\n    return len(t) > 0\n",
        "routes.py": "def handle_request(req):\n    return login(req.user, req.pw)\n",
    }
    fetcher = _StubFetcher(contents)
    ctx = ImplementationContext(
        owner="o", repo="r", repo_handle=_handle(), fetcher=fetcher, request_text="x",
    )
    ctx.relevant_files = [
        ScoredFile(path="auth.py", score=10.0, matched_symbols=["login"]),
        ScoredFile(path="routes.py", score=8.0),
    ]
    bundle = build_implementation_context(ctx)
    assert "auth.py::login" in bundle["dependency_context"]
    callers = bundle["dependency_context"]["auth.py::login"]["callers"]
    assert any(c["name"] == "handle_request" for c in callers)


def test_context_bundle_skips_unfetchable_files_without_crashing():
    fetcher = _StubFetcher({})
    ctx = ImplementationContext(
        owner="o", repo="r", repo_handle=_handle(), fetcher=fetcher, request_text="x",
    )
    ctx.relevant_files = [ScoredFile(path="missing.py", score=1.0)]
    bundle = build_implementation_context(ctx)
    assert bundle["files"] == []


def test_config_files_are_collected():
    contents = {"config.py": "DEBUG = True\n"}
    fetcher = _StubFetcher(contents)
    ctx = ImplementationContext(
        owner="o", repo="r", repo_handle=_handle(), fetcher=fetcher, request_text="x",
    )
    ctx.relevant_files = [ScoredFile(path="config.py", score=3.0, is_config=True)]
    bundle = build_implementation_context(ctx)
    assert "config.py" in bundle["config_files"]
