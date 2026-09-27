"""
End-to-end pipeline test -- the closest thing to the spec's "Final
Acceptance Test" runnable fully offline: a real local git repo with an
intentionally buggy commit, run through all 8 agents, with a
FakeLLMProvider standing in for the real Anthropic API (since this
sandbox has no network access to call it for real). Verification still
runs for real via `git apply` + `python -m unittest discover` against
the local repo.
"""
import json
import os
import subprocess
import tempfile

from app.agents.base import AnalysisContext
from app.agents.orchestrator import AgentPipeline
from app.github.client import CommitDetail, FileChange, GitHubClient
from app.llm.provider import LLMProvider, LLMResponse


BUGGY_SOURCE = "def add(a, b):\n    return a - b  # BUG: should add, not subtract\n"
FIXED_SOURCE = "def add(a, b):\n    return a + b\n"
TEST_SOURCE = (
    "import unittest\nfrom calc import add\n\n"
    "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
)


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def _build_local_repo():
    repo_dir = tempfile.mkdtemp(prefix="e2e_repo_")
    _run(["git", "init", "--quiet"], cwd=repo_dir)
    _run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir)
    _run(["git", "config", "user.name", "T"], cwd=repo_dir)

    with open(os.path.join(repo_dir, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return 0\n")
    with open(os.path.join(repo_dir, "test_calc.py"), "w") as f:
        f.write(TEST_SOURCE)
    with open(os.path.join(repo_dir, "setup.py"), "w") as f:
        f.write("from setuptools import setup\nsetup(name='calc')\n")
    _run(["git", "add", "."], cwd=repo_dir)
    _run(["git", "commit", "--quiet", "-m", "Initial implementation"], cwd=repo_dir)

    with open(os.path.join(repo_dir, "calc.py"), "w") as f:
        f.write(BUGGY_SOURCE)
    _run(["git", "add", "."], cwd=repo_dir)
    _run(["git", "commit", "--quiet", "-m", "Introduce intentional bug in add()"], cwd=repo_dir)
    buggy_sha = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()

    return repo_dir, buggy_sha


class FakeGitHubClient(GitHubClient):
    """Stands in for real GitHub API calls; the pipeline only needs
    populate_file_versions (already done by the caller here) and
    get_clone_url / get_file_content."""

    def __init__(self, repo_dir):
        super().__init__(token="")
        self.repo_dir = repo_dir

    def populate_file_versions(self, owner, repo, commit):
        pass  # already populated by the test setup

    def get_file_content(self, owner, repo, path, ref):
        return None  # no extra test-file discovery needed for this test

    def get_clone_url(self, owner, repo):
        return f"file://{self.repo_dir}"


class FakeLLMProvider(LLMProvider):
    """Deterministic stand-in for Claude: inspects the system prompt to
    decide which stage is calling it, and returns a plausible structured
    response with no tool calls (so the agent loops terminate immediately)."""

    def complete(self, system, messages, tools=None, max_tokens=4096):
        if "senior code reviewer" in system:
            issues = [
                {
                    "file": "calc.py",
                    "function": "add",
                    "line": 2,
                    "severity": "CRITICAL",
                    "category": "CORRECTNESS",
                    "title": "add() subtracts instead of adding",
                    "description": "The function is named add() but its body subtracts b from a.",
                    "evidence": "return a - b",
                    "impact": "Any caller relying on addition semantics gets silently wrong results.",
                    "confidence": 0.97,
                }
            ]
            return LLMResponse(text=json.dumps(issues), tool_calls=[], stop_reason="end_turn")

        if "skeptical debugging agent" in system:
            verdict = {
                "confirmed": True,
                "revised_confidence": 0.98,
                "refined_evidence": "Confirmed: `return a - b` in a function named add().",
                "refined_impact": "Every addition in the codebase silently returns the wrong value.",
            }
            return LLMResponse(text=json.dumps(verdict), tool_calls=[], stop_reason="end_turn")

        if "implementation-ready fix" in system:
            fix = {
                "explanation": "Change subtraction to addition.",
                "existing_code": BUGGY_SOURCE,
                "suggested_code": FIXED_SOURCE,
                "unified_diff": (
                    "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    " def add(a, b):\n"
                    "-    return a - b  # BUG: should add, not subtract\n"
                    "+    return a + b\n"
                ),
                "expected_impact": "add() now returns the correct sum.",
                "tests_required": "test_calc.py::T::test_add",
            }
            return LLMResponse(text=json.dumps(fix), tool_calls=[], stop_reason="end_turn")

        return LLMResponse(text="[]", tool_calls=[], stop_reason="end_turn")


def test_full_pipeline_detects_bug_generates_and_verifies_fix():
    repo_dir, buggy_sha = _build_local_repo()
    os.environ["ALLOWED_TEST_COMMANDS"] = "python -m unittest discover,python3 -m unittest discover"
    from app.config import get_settings

    get_settings.cache_clear()

    commit = CommitDetail(
        sha=buggy_sha,
        message="Introduce intentional bug in add()",
        author_name="T",
        author_email="t@example.com",
        authored_at="2026-01-01T00:00:00Z",
        parent_sha="parent",
        additions=1,
        deletions=1,
        files=[
            FileChange(
                filename="calc.py",
                status="modified",
                additions=1,
                deletions=1,
                patch="@@ -1,2 +1,2 @@\n-    return 0\n+    return a - b",
                previous_content="def add(a, b):\n    return 0\n",
                current_content=BUGGY_SOURCE,
            )
        ],
    )

    ctx = AnalysisContext(owner="demo", repo="calc-demo", commit=commit)
    github = FakeGitHubClient(repo_dir)
    llm = FakeLLMProvider()
    pipeline = AgentPipeline(github, llm, run_verification=True)

    result_ctx = pipeline.run(ctx)

    assert len(result_ctx.changed_symbols) == 1
    assert result_ctx.changed_symbols[0].symbol_name == "add"

    assert len(result_ctx.issues) == 1
    issue = result_ctx.issues[0]
    assert issue["severity"] == "CRITICAL"
    assert issue["suggested_patch"]

    assert result_ctx.review_status == "NEEDS_CHANGES"

    assert len(result_ctx.verification_results) == 1
    verification = result_ctx.verification_results[0]
    assert verification["status"] == "VERIFIED", verification["details"]
    assert verification["tests_passed"] >= 1

    print("Pipeline trace:")
    for entry in result_ctx.trace:
        print(f"  [{entry['agent']}] {entry['message']}")


if __name__ == "__main__":
    test_full_pipeline_detects_bug_generates_and_verifies_fix()
    print("\nEND-TO-END PIPELINE TEST PASSED")
