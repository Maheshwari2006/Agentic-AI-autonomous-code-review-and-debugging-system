from app.github.tree_parser import parse_tree


def _raw_tree():
    return [
        {"path": "backend", "type": "tree"},
        {"path": "backend/app", "type": "tree"},
        {"path": "backend/app/main.py", "type": "blob", "size": 500},
        {"path": "backend/app/api/users.py", "type": "blob", "size": 800},
        {"path": "backend/tests/test_users.py", "type": "blob", "size": 300},
        {"path": "backend/app/config.py", "type": "blob", "size": 200},
        {"path": "requirements.txt", "type": "blob", "size": 50},
        {"path": ".git", "type": "tree"},
        {"path": ".git/HEAD", "type": "blob", "size": 20},
        {"path": "node_modules", "type": "tree"},
        {"path": "node_modules/foo/index.js", "type": "blob", "size": 10},
        {"path": "assets/logo.png", "type": "blob", "size": 4000},
        {"path": "__pycache__/module.pyc", "type": "blob", "size": 10},
    ]


def test_ignored_directories_are_excluded():
    parsed = parse_tree(_raw_tree())
    paths = {f.path for f in parsed.files}
    assert not any(p.startswith(".git/") for p in paths)
    assert not any(p.startswith("node_modules/") for p in paths)
    assert not any(p.startswith("__pycache__/") for p in paths)


def test_binary_extensions_excluded():
    parsed = parse_tree(_raw_tree())
    paths = {f.path for f in parsed.files}
    assert "assets/logo.png" not in paths


def test_test_file_detection():
    parsed = parse_tree(_raw_tree())
    by_path = {f.path: f for f in parsed.files}
    assert by_path["backend/tests/test_users.py"].is_test is True
    assert by_path["backend/app/main.py"].is_test is False


def test_config_file_detection():
    parsed = parse_tree(_raw_tree())
    by_path = {f.path: f for f in parsed.files}
    assert by_path["backend/app/config.py"].is_config is True
    assert by_path["requirements.txt"].is_config is True


def test_directories_tracked_separately():
    parsed = parse_tree(_raw_tree())
    assert "backend/app" in parsed.directories
