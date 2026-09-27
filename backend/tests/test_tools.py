from app.agents.tools import ToolExecutor
from app.agents.base import AnalysisContext
from app.github.client import CommitDetail, FileChange
from app.analysis.change_detection import detect_changed_symbols
from app.analysis.dependency import SymbolIndex


def _build_ctx():
    old_src = "def login(u, p):\n    return True\n"
    new_src = (
        "def login(u, p):\n    return validate_token(p)\n\n"
        "def validate_token(t):\n    return len(t) > 0\n"
    )
    commit = CommitDetail(
        sha="abc123",
        message="Fix login",
        author_name="Alice",
        author_email="a@example.com",
        authored_at="2026-01-01T00:00:00Z",
        parent_sha="parent1",
        additions=4,
        deletions=1,
        files=[
            FileChange(
                filename="auth.py",
                status="modified",
                additions=4,
                deletions=1,
                patch="@@ ... @@",
                previous_content=old_src,
                current_content=new_src,
            )
        ],
    )
    ctx = AnalysisContext(owner="o", repo="r", commit=commit)
    ctx.changed_symbols = detect_changed_symbols("auth.py", old_src, new_src)
    ctx.index.add_file("auth.py", new_src)
    ctx.extra_file_contents["auth.py"] = new_src
    return ctx


def test_list_changed_files():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("list_changed_files", {})
    assert result["files"][0]["path"] == "auth.py"


def test_read_file():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("read_file", {"path": "auth.py"})
    assert "validate_token" in result["content"]


def test_read_file_missing_returns_error_not_exception():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("read_file", {"path": "does_not_exist.py"})
    assert "error" in result


def test_read_function_current_revision():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute(
        "read_function", {"path": "auth.py", "qualified_name": "validate_token", "revision": "current"}
    )
    assert "def validate_token" in result["source"]


def test_find_callers():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("find_callers", {"symbol_name": "validate_token"})
    assert any(c["qualified_name"] == "login" for c in result["callers"])


def test_search_code():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("search_code", {"query": "validate_token"})
    assert result["results"][0]["path"] == "auth.py"
    assert len(result["results"][0]["matches"]) > 0


def test_unknown_tool_returns_error():
    ctx = _build_ctx()
    executor = ToolExecutor(ctx)
    result = executor.execute("delete_everything", {})
    assert "error" in result
