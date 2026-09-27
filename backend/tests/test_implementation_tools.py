from app.agents.implementation_tools import ImplementationToolExecutor
from app.implementation.context import ImplementationContext
from app.github.repository_fetcher import RepositoryHandle
from app.github.tree_parser import ParsedTree, RepoFile


class _StubFetcher:
    def __init__(self, contents: dict):
        self._contents = contents
        self._cache = {}

    def get(self, path):
        if path not in self._cache:
            self._cache[path] = self._contents.get(path)
        return self._cache[path]

    def cached_paths(self):
        return [p for p, c in self._cache.items() if c is not None]


def _ctx(contents, files):
    handle = RepositoryHandle(
        owner="o", repo="r", ref="main", resolved_sha="a" * 40,
        default_branch="main", html_url="url",
        tree=ParsedTree(files=files, directories=["backend", "backend/app"]),
    )
    return ImplementationContext(
        owner="o", repo="r", repo_handle=handle, fetcher=_StubFetcher(contents),
        request_text="add auth",
    )


def _repo_file(path):
    return RepoFile(path=path, extension="py", size=100, depth=path.count("/"), is_test=False, is_config=False)


def test_read_file_fetches_and_indexes():
    contents = {"backend/app/auth.py": "def login():\n    return validate()\n\ndef validate():\n    return True\n"}
    ctx = _ctx(contents, [_repo_file("backend/app/auth.py")])
    executor = ImplementationToolExecutor(ctx)
    result = executor.execute("read_file", {"path": "backend/app/auth.py"})
    assert "def login" in result["content"]


def test_read_file_missing_returns_error():
    ctx = _ctx({}, [])
    executor = ImplementationToolExecutor(ctx)
    result = executor.execute("read_file", {"path": "nope.py"})
    assert "error" in result


def test_read_function_after_read_file():
    contents = {"backend/app/auth.py": "def login():\n    return validate()\n\ndef validate():\n    return True\n"}
    ctx = _ctx(contents, [_repo_file("backend/app/auth.py")])
    executor = ImplementationToolExecutor(ctx)
    executor.execute("read_file", {"path": "backend/app/auth.py"})
    result = executor.execute("read_function", {"path": "backend/app/auth.py", "qualified_name": "validate"})
    assert "return True" in result["source"]


def test_find_callers_across_read_files():
    contents = {
        "auth.py": "def login():\n    return validate()\n\ndef validate():\n    return True\n",
        "routes.py": "def handle():\n    return login()\n",
    }
    ctx = _ctx(contents, [_repo_file("auth.py"), _repo_file("routes.py")])
    executor = ImplementationToolExecutor(ctx)
    executor.execute("read_file", {"path": "auth.py"})
    executor.execute("read_file", {"path": "routes.py"})
    result = executor.execute("find_callers", {"symbol_name": "login"})
    assert any(c["qualified_name"] == "handle" for c in result["callers"])


def test_search_filenames():
    ctx = _ctx({}, [_repo_file("backend/app/auth/jwt_utils.py"), _repo_file("backend/app/api/orders.py")])
    executor = ImplementationToolExecutor(ctx)
    result = executor.execute("search_filenames", {"query": "jwt"})
    assert "backend/app/auth/jwt_utils.py" in result["matches"]
    assert "backend/app/api/orders.py" not in result["matches"]


def test_list_directory():
    ctx = _ctx({}, [_repo_file("backend/app/main.py"), _repo_file("backend/app/api/users.py")])
    executor = ImplementationToolExecutor(ctx)
    result = executor.execute("list_directory", {"path": "backend/app"})
    names = {e["name"] for e in result["entries"]}
    assert "main.py" in names
    assert "api" in names


def test_unknown_tool_returns_error():
    ctx = _ctx({}, [])
    executor = ImplementationToolExecutor(ctx)
    result = executor.execute("delete_repo", {})
    assert "error" in result
