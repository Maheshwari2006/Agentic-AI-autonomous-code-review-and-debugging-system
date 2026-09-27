import os
import subprocess
import tempfile

import pytest

from app.services.verification_runner import VerificationRunner


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def local_repo():
    """Builds a tiny real git repo on disk with a buggy function and a
    failing test, entirely offline (file:// clone URL) so the real
    VerificationRunner.verify() can be exercised end-to-end without
    network access."""
    src_dir = tempfile.mkdtemp(prefix="verify_src_")
    _run(["git", "init", "--quiet"], cwd=src_dir)
    _run(["git", "config", "user.email", "test@example.com"], cwd=src_dir)
    _run(["git", "config", "user.name", "Test"], cwd=src_dir)

    with open(os.path.join(src_dir, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return a - b  # BUG: should be a + b\n")

    with open(os.path.join(src_dir, "test_calc.py"), "w") as f:
        f.write("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    with open(os.path.join(src_dir, "setup.py"), "w") as f:
        f.write("from setuptools import setup\nsetup(name='calc')\n")

    _run(["git", "add", "."], cwd=src_dir)
    _run(["git", "commit", "--quiet", "-m", "Introduce intentional bug"], cwd=src_dir)
    sha = _run(["git", "rev-parse", "HEAD"], cwd=src_dir).stdout.strip()

    yield src_dir, sha


def _pytest_available():
    try:
        subprocess.run(["python3", "-m", "pytest", "--version"], capture_output=True, timeout=10)
        return True
    except FileNotFoundError:
        return False


def test_verify_without_patch_reports_failure_or_unable(local_repo, monkeypatch):
    src_dir, sha = local_repo
    monkeypatch.setenv("ALLOWED_TEST_COMMANDS", "python3 -m pytest")
    from app.config import get_settings

    get_settings.cache_clear()

    runner = VerificationRunner(clone_url=f"file://{src_dir}", commit_sha=sha)
    outcome = runner.verify(patch_text=None)

    # Either pytest isn't installed here (UNABLE_TO_VERIFY) or it is and
    # correctly reports the bug as a failing test (FAILED). Both are
    # honest outcomes; VERIFIED would be wrong here since the bug is real.
    assert outcome.status in ("FAILED", "UNABLE_TO_VERIFY", "PARTIALLY_VERIFIED")
    assert outcome.status != "VERIFIED"


def test_verify_with_correct_patch(local_repo, monkeypatch):
    src_dir, sha = local_repo
    monkeypatch.setenv("ALLOWED_TEST_COMMANDS", "python3 -m pytest")
    from app.config import get_settings

    get_settings.cache_clear()

    patch = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b  # BUG: should be a + b\n"
        "+    return a + b\n"
    )
    runner = VerificationRunner(clone_url=f"file://{src_dir}", commit_sha=sha)
    outcome = runner.verify(patch_text=patch)

    if outcome.status == "UNABLE_TO_VERIFY" and "pytest" in outcome.details.lower():
        pytest.skip("pytest not installed in this environment")
    assert outcome.status == "VERIFIED"
    assert outcome.tests_passed >= 1


def test_verify_with_bad_patch_reports_unable_to_verify(local_repo, monkeypatch):
    src_dir, sha = local_repo
    monkeypatch.setenv("ALLOWED_TEST_COMMANDS", "python3 -m pytest")
    from app.config import get_settings

    get_settings.cache_clear()

    bad_patch = "this is not a valid unified diff at all"
    runner = VerificationRunner(clone_url=f"file://{src_dir}", commit_sha=sha)
    outcome = runner.verify(patch_text=bad_patch)
    assert outcome.status == "UNABLE_TO_VERIFY"
    assert "patch" in outcome.details.lower()


def test_verify_bad_clone_url_is_unable_to_verify(monkeypatch):
    monkeypatch.setenv("ALLOWED_TEST_COMMANDS", "python3 -m pytest")
    from app.config import get_settings

    get_settings.cache_clear()
    runner = VerificationRunner(clone_url="/nonexistent/path/does/not/exist", commit_sha="deadbeef")
    outcome = runner.verify()
    assert outcome.status == "UNABLE_TO_VERIFY"
