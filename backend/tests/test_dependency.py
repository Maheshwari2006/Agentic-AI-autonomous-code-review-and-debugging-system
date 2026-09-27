from app.analysis.dependency import SymbolIndex, build_context_bundle


def _make_index():
    idx = SymbolIndex()
    idx.add_file(
        "auth.py",
        "def login(u, p):\n    return validate_token(p)\n\n"
        "def validate_token(t):\n    return len(t) > 0\n",
    )
    idx.add_file(
        "routes.py",
        "from auth import login\n\ndef handle_request(req):\n    return login(req.user, req.pw)\n",
    )
    return idx


def test_index_finds_callers_across_files():
    idx = _make_index()
    callers = idx.find_callers("login")
    assert len(callers) == 1
    assert callers[0].file_path == "routes.py"
    assert callers[0].symbol.qualified_name == "handle_request"


def test_index_finds_callees():
    idx = _make_index()
    callees = idx.find_callees("auth.py", "login")
    assert len(callees) == 1
    assert callees[0].symbol.qualified_name == "validate_token"


def test_context_bundle_has_full_source_not_truncated():
    idx = _make_index()
    bundle = build_context_bundle(idx, "login", "auth.py", "login")
    assert bundle["callers"][0]["source"].strip().startswith("def handle_request")
    assert bundle["callees"][0]["source"].strip().startswith("def validate_token")


def test_stats_report_indexed_counts():
    idx = _make_index()
    stats = idx.stats()
    assert stats["files_indexed"] == 2
    assert stats["symbols_indexed"] == 3
