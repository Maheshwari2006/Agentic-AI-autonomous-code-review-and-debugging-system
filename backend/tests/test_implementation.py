"""
Tests for the AI code fetching + implementation suggestion feature's
non-network modules: tree parsing, file discovery/relevance scoring,
context building, diff construction/validation, suggestion filtering,
and plan/patch assembly.

These exercise real logic against the acceptance fixture repository at
backend/tests/fixtures/acceptance_repo/ (spec section 19): a small real
Python package with models/auth/api/utils modules, an existing test
file, and two intentional gaps -- `get_user` has no auth check, and
`chunk_list` has an off-by-one bug -- so file discovery and context
building have something real to find relevance in.

No network access is used or required.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "acceptance_repo")


def _fixture_paths():
    paths = []
    for root, _dirs, files in os.walk(FIXTURE_ROOT):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, FIXTURE_ROOT).replace(os.sep, "/")
            paths.append(rel)
    return sorted(paths)


class FakeFileFetcher:
    """Test double with the same interface as github/file_fetcher.py's
    FileFetcher, backed by the local fixture directory instead of a real
    GitHub API call."""

    def __init__(self):
        self._cache = {}

    def get(self, path):
        if path in self._cache:
            return self._cache[path]
        full = os.path.join(FIXTURE_ROOT, path)
        content = None
        if os.path.isfile(full):
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
        self._cache[path] = content
        return content

    def get_many(self, paths):
        return {p: c for p in paths if (c := self.get(p)) is not None}

    def cached_paths(self):
        return [p for p, c in self._cache.items() if c is not None]

    def stats(self):
        fetched = [p for p, c in self._cache.items() if c is not None]
        return {"fetched": len(fetched), "unavailable": len(self._cache) - len(fetched)}


def _fake_tree():
    from app.github.tree_parser import RepoFile, ParsedTree, _looks_like_test, _looks_like_config, _extension_of

    files = []
    for path in _fixture_paths():
        files.append(RepoFile(
            path=path,
            extension=_extension_of(path),
            size=100,
            depth=path.count("/"),
            is_test=_looks_like_test(path),
            is_config=_looks_like_config(path),
        ))
    return ParsedTree(files=files, directories=[])


# --------------------------------------------------------------------- #
# tree_parser
# --------------------------------------------------------------------- #
def test_parse_tree_filters_ignored_dirs_and_binaries():
    from app.github.tree_parser import parse_tree

    raw = [
        {"path": "app/api.py", "type": "blob", "size": 10},
        {"path": "node_modules/foo/index.js", "type": "blob", "size": 10},
        {"path": ".git/HEAD", "type": "blob", "size": 10},
        {"path": "assets/logo.png", "type": "blob", "size": 10},
        {"path": "app", "type": "tree", "size": 0},
    ]
    parsed = parse_tree(raw)
    paths = [f.path for f in parsed.files]
    assert "app/api.py" in paths
    assert "node_modules/foo/index.js" not in paths
    assert ".git/HEAD" not in paths
    assert "assets/logo.png" not in paths
    assert "app" in parsed.directories


def test_parse_tree_flags_tests_and_config():
    from app.github.tree_parser import parse_tree

    raw = [
        {"path": "tests/test_auth.py", "type": "blob", "size": 10},
        {"path": "requirements.txt", "type": "blob", "size": 10},
        {"path": "app/models.py", "type": "blob", "size": 10},
    ]
    parsed = parse_tree(raw)
    by_path = {f.path: f for f in parsed.files}
    assert by_path["tests/test_auth.py"].is_test
    assert by_path["requirements.txt"].is_config
    assert not by_path["app/models.py"].is_test


# --------------------------------------------------------------------- #
# file_discovery
# --------------------------------------------------------------------- #
def test_discover_relevant_files_ranks_auth_and_api_highest():
    from app.implementation.file_discovery import discover_relevant_files

    tree = _fake_tree()
    fetcher = FakeFileFetcher()
    ranked = discover_relevant_files(tree, "Add JWT authentication to the user API", fetcher)

    ranked_paths = [r.path for r in ranked]
    assert "app/auth.py" in ranked_paths
    assert "app/api.py" in ranked_paths
    # auth.py / api.py should outrank an unrelated utility module
    utils_score = next((r.score for r in ranked if r.path == "app/utils.py"), -1)
    auth_score = next((r.score for r in ranked if r.path == "app/auth.py"), -1)
    assert auth_score > utils_score


def test_discover_relevant_files_always_includes_explicit_target():
    from app.implementation.file_discovery import discover_relevant_files

    tree = _fake_tree()
    fetcher = FakeFileFetcher()
    ranked = discover_relevant_files(
        tree, "completely unrelated request about nothing in particular",
        fetcher, target_file="app/utils.py",
    )
    assert any(r.path == "app/utils.py" for r in ranked)


def test_discover_relevant_files_never_returns_every_file():
    """Regression guard for spec section 4's 'do not simply return every
    repository file' requirement, on an irrelevant request."""
    from app.implementation.file_discovery import discover_relevant_files

    tree = _fake_tree()
    fetcher = FakeFileFetcher()
    ranked = discover_relevant_files(tree, "xyzzy plugh unrelated nonsense", fetcher)
    assert len(ranked) < len(tree.files)


# --------------------------------------------------------------------- #
# context_builder + AST integration
# --------------------------------------------------------------------- #
def test_build_implementation_context_indexes_symbols_and_finds_tests():
    from app.implementation.context import ImplementationContext
    from app.implementation.context_builder import build_implementation_context
    from app.implementation.file_discovery import discover_relevant_files
    from app.github.repository_fetcher import RepositoryHandle

    tree = _fake_tree()
    fetcher = FakeFileFetcher()
    handle = RepositoryHandle(
        owner="acme", repo="widgets", ref="main", resolved_sha="a" * 40,
        default_branch="main", html_url="https://github.com/acme/widgets", tree=tree,
    )
    ctx = ImplementationContext(
        owner="acme", repo="widgets", repo_handle=handle, fetcher=fetcher,
        request_text="Add JWT authentication to the user API",
    )
    ctx.relevant_files = discover_relevant_files(tree, ctx.request_text, fetcher)
    bundle = build_implementation_context(ctx)

    assert bundle["files"], "expected at least one relevant file in the bundle"
    api_file = next((f for f in bundle["files"] if f["path"] == "app/api.py"), None)
    assert api_file is not None
    symbol_names = [s["qualified_name"] for s in api_file["symbols"]]
    assert "get_user" in symbol_names
    # never truncates -- full function source must be present verbatim
    assert "def get_user(user_id):" in api_file["content"]


# --------------------------------------------------------------------- #
# diff_builder / patch_generator
# --------------------------------------------------------------------- #
def test_build_new_file_diff_is_valid_and_applies_structurally():
    from app.implementation.diff_builder import build_new_file_diff, validate_unified_diff

    diff = build_new_file_diff("app/jwt_utils.py", "def issue_token(user):\n    pass\n")
    result = validate_unified_diff(diff)
    assert result.valid
    assert "app/jwt_utils.py" in result.file_paths


def test_validate_unified_diff_rejects_garbage():
    from app.implementation.diff_builder import validate_unified_diff

    result = validate_unified_diff("this is not a diff at all")
    assert not result.valid
    assert result.errors


def test_generate_patch_adds_missing_new_file_diffs_deterministically():
    from app.implementation.patch_generator import generate_patch

    model_patch = (
        "--- a/app/api.py\n+++ b/app/api.py\n@@ -1,2 +1,3 @@\n"
        " from .models import get_user_by_id\n+from .auth import require_auth\n"
    )
    files_to_create = [{"path": "app/jwt_utils.py", "content": "def issue_token(u):\n    pass\n", "reason": "new module"}]
    result = generate_patch(model_patch, files_to_create)

    assert result.is_syntactically_valid
    assert "app/jwt_utils.py" in result.new_files_added_deterministically
    assert "app/api.py" in result.files_touched
    assert "app/jwt_utils.py" in result.files_touched


def test_generate_patch_does_not_duplicate_file_already_in_model_patch():
    from app.implementation.patch_generator import generate_patch

    model_patch = "--- /dev/null\n+++ b/app/jwt_utils.py\n@@ -0,0 +1,1 @@\n+def issue_token(u): pass\n"
    files_to_create = [{"path": "app/jwt_utils.py", "content": "def issue_token(u): pass\n"}]
    result = generate_patch(model_patch, files_to_create)
    assert result.new_files_added_deterministically == []


# --------------------------------------------------------------------- #
# suggestion_engine
# --------------------------------------------------------------------- #
def test_suggestion_engine_drops_vague_suggestions():
    from app.implementation.suggestion_engine import build_suggestions

    raw = [
        {
            "file": "app/api.py", "symbol": "get_user", "location": "top of function body",
            "change_type": "required",
            "current_behavior": "No authentication check before returning user data.",
            "proposed_change": "Add a require_auth(request) dependency call before fetching the user.",
            "reason": "The endpoint currently has no authentication dependency.",
            "expected_behavior": "Requests without a valid JWT should return HTTP 401.",
        },
        {"file": "app/api.py", "proposed_change": "Improve security.", "reason": "it's not secure"},
        {"proposed_change": "Add auth", "reason": "needed"},  # missing file
    ]
    kept = build_suggestions(raw)
    assert len(kept) == 1
    assert kept[0].file == "app/api.py"
    assert kept[0].change_type == "required"


def test_suggestion_engine_filter_report_counts_dropped():
    from app.implementation.suggestion_engine import filter_report

    raw = [{"file": "x.py", "proposed_change": "Improve security.", "reason": "bad"}]
    report = filter_report(raw)
    assert report["dropped_count"] == 1
    assert report["suggestions"] == []


# --------------------------------------------------------------------- #
# implementation_planner
# --------------------------------------------------------------------- #
def test_build_plan_fills_defaults_for_missing_fields():
    from app.implementation.implementation_planner import build_plan

    plan = build_plan({"objective": "Add JWT auth"}, tests_to_add=["test_jwt_auth.py"], agent_risks=["breaks anonymous access"])
    assert plan.objective == "Add JWT auth"
    assert plan.assumptions == []
    assert plan.tests == ["test_jwt_auth.py"]
    assert plan.risks == ["breaks anonymous access"]


def test_build_plan_handles_empty_input():
    from app.implementation.implementation_planner import build_plan

    plan = build_plan(None)
    assert plan.objective == ""
    assert plan.to_dict()["files_to_modify"] == []
