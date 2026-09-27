from app.github.tree_parser import parse_tree
from app.implementation.file_discovery import discover_relevant_files


class _StubFetcher:
    """Minimal stand-in for FileFetcher -- discover_relevant_files only
    calls get_many(paths), so that's all this needs to implement."""

    def __init__(self, contents: dict):
        self._contents = contents

    def get_many(self, paths):
        return {p: self._contents[p] for p in paths if p in self._contents}


def _tree():
    raw = [
        {"path": "backend/app/api/users.py", "type": "blob", "size": 400},
        {"path": "backend/app/auth/jwt_utils.py", "type": "blob", "size": 300},
        {"path": "backend/app/models/user.py", "type": "blob", "size": 250},
        {"path": "backend/app/api/orders.py", "type": "blob", "size": 350},
        {"path": "backend/tests/test_users.py", "type": "blob", "size": 150},
        {"path": "README.md", "type": "blob", "size": 100},
    ]
    return parse_tree(raw)


def test_relevant_files_ranked_above_unrelated():
    tree = _tree()
    fetcher = _StubFetcher({
        "backend/app/api/users.py": "def get_user():\n    pass\n",
        "backend/app/auth/jwt_utils.py": "def encode_jwt(payload):\n    pass\n",
    })
    ranked = discover_relevant_files(tree, "Add JWT authentication to the user API", fetcher)
    paths = [r.path for r in ranked]
    assert "backend/app/api/users.py" in paths
    assert "backend/app/auth/jwt_utils.py" in paths
    # orders.py has no token overlap with the request and should rank
    # below (or be excluded from) the clearly relevant files.
    orders_index = paths.index("backend/app/api/orders.py") if "backend/app/api/orders.py" in paths else len(paths)
    users_index = paths.index("backend/app/api/users.py")
    assert users_index < orders_index


def test_explicit_target_file_always_included():
    tree = _tree()
    fetcher = _StubFetcher({})
    ranked = discover_relevant_files(
        tree, "completely unrelated request text", fetcher, target_file="README.md",
    )
    assert any(r.path == "README.md" for r in ranked)


def test_does_not_return_every_file():
    tree = _tree()
    fetcher = _StubFetcher({})
    ranked = discover_relevant_files(tree, "xyz nonexistent feature qqq", fetcher)
    assert len(ranked) < len(tree.files)


def test_symbol_name_match_boosts_score():
    tree = _tree()
    fetcher = _StubFetcher({
        "backend/app/api/users.py": "def get_user():\n    pass\n",
        "backend/app/auth/jwt_utils.py": "def encode_jwt(payload):\n    return payload\n",
        "backend/app/models/user.py": "class User:\n    pass\n",
        "backend/app/api/orders.py": "def list_orders():\n    pass\n",
    })
    ranked = discover_relevant_files(tree, "encode a jwt token", fetcher)
    jwt_file = next(r for r in ranked if r.path == "backend/app/auth/jwt_utils.py")
    assert "encode_jwt" in jwt_file.matched_symbols
