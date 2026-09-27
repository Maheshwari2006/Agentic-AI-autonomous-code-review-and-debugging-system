from app.analysis.change_detection import detect_changed_symbols, group_changes


OLD_AUTH = """
def login(user, pw):
    return True
"""

NEW_AUTH = """
def login(user, pw):
    if not pw:
        return False
    return True

def validate_token(tok):
    return len(tok) > 0
"""


def test_detects_modified_and_added_symbols():
    changes = detect_changed_symbols("auth.py", OLD_AUTH, NEW_AUTH)
    by_name = {c.symbol_name: c for c in changes}
    assert by_name["login"].change_type == "modified"
    assert by_name["validate_token"].change_type == "added"


def test_detects_removed_symbol():
    changes = detect_changed_symbols("auth.py", NEW_AUTH, OLD_AUTH)
    by_name = {c.symbol_name: c for c in changes}
    assert by_name["validate_token"].change_type == "removed"


def test_unchanged_symbol_is_not_reported():
    src = "def stable():\n    return 42\n"
    changes = detect_changed_symbols("m.py", src, src)
    assert changes == []


def test_multiple_independent_changes_form_separate_groups():
    """A single commit touching two files with no call relationship
    between them should split into two independent change groups --
    the 'multiple unrelated changes in one commit' requirement."""
    old_a = "def login(u, p):\n    return True\n"
    new_a = "def login(u, p):\n    return bool(p)\n"
    old_b = "def write_log(msg):\n    print(msg)\n"
    new_b = "def write_log(msg):\n    print('[LOG]', msg)\n"

    changes = []
    changes += detect_changed_symbols("auth.py", old_a, new_a)
    changes += detect_changed_symbols("logging.py", old_b, new_b)

    groups = group_changes(changes)
    assert len(groups) == 2
    files_per_group = [{c.file_path for c in g} for g in groups]
    assert {"auth.py"} in files_per_group
    assert {"logging.py"} in files_per_group


def test_related_changes_in_same_file_form_one_group():
    changes = detect_changed_symbols("auth.py", OLD_AUTH, NEW_AUTH)
    groups = group_changes(changes)
    assert len(groups) == 1
    assert len(groups[0]) == 2
