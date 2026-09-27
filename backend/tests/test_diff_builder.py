from app.implementation.diff_builder import build_new_file_diff, combine_diffs, validate_unified_diff


def test_build_new_file_diff_is_valid():
    diff = build_new_file_diff("pkg/new_module.py", "def f():\n    return 1\n")
    assert diff.startswith("--- /dev/null")
    assert "+++ b/pkg/new_module.py" in diff
    result = validate_unified_diff(diff)
    assert result.valid
    assert result.file_paths == ["pkg/new_module.py"]


def test_validate_accepts_well_formed_diff():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "+import sys\n"
        " print(os)\n"
    )
    result = validate_unified_diff(diff)
    assert result.valid
    assert result.file_paths == ["x.py"]


def test_validate_rejects_missing_hunk():
    diff = "--- a/x.py\n+++ b/x.py\n"
    result = validate_unified_diff(diff)
    assert not result.valid
    assert result.errors


def test_validate_rejects_garbage():
    result = validate_unified_diff("this is not a diff at all")
    assert not result.valid


def test_empty_diff_is_valid_no_op():
    result = validate_unified_diff("")
    assert result.valid
    assert result.file_paths == []


def test_combine_diffs_joins_multiple_blocks():
    d1 = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    d2 = build_new_file_diff("y.py", "content\n")
    combined = combine_diffs(d1, d2)
    result = validate_unified_diff(combined)
    assert result.valid
    assert set(result.file_paths) == {"x.py", "y.py"}
