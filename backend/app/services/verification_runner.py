"""
VerificationRunner -- isolated, safety-bounded execution of tests to check
a proposed fix.

Safety measures (per spec section 13):
  - Runs entirely inside a fresh temporary directory (tempfile.mkdtemp),
    deleted afterward.
  - `subprocess.run(..., timeout=...)` enforces a hard wall-clock limit.
  - The subprocess environment is stripped down to a minimal allowlist of
    variables (PATH, LANG, HOME set to the temp dir) so the user's real
    secrets/environment are never exposed to repository code.
  - Only commands present in `ALLOWED_TEST_COMMANDS` (config.py) may be
    run -- arbitrary shell strings from an LLM response are never executed
    directly; we only ever run a fixed, allowlisted command.
  - Network access during the test run is not specifically sandboxed here
    (Python's subprocess doesn't provide that in a portable way) --
    deployers who need a hard network boundary should run this service
    itself inside a locked-down container/VM. This is called out in the
    README's security notes rather than silently assumed.

This module is a clearly separate "CODE EXECUTION" boundary from the rest
of the (purely read-only, analysis-only) pipeline.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from ..config import get_settings, get_allowed_test_commands
from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationOutcome:
    status: str  # VERIFIED | PARTIALLY_VERIFIED | FAILED | UNABLE_TO_VERIFY
    tests_passed: int = 0
    tests_failed: int = 0
    details: str = ""
    command_run: str = ""
    # Explicitly tracked rather than inferred from `status`/`details` by
    # callers: UNABLE_TO_VERIFY can happen either because the patch never
    # applied, or because it applied fine but no test runner was
    # available -- those are different facts callers (e.g. the
    # implementation-feature API) need to distinguish.
    patch_applied: bool = False


class VerificationRunner:
    def __init__(self, clone_url: str, commit_sha: str):
        self.clone_url = clone_url
        self.commit_sha = commit_sha
        self.settings = get_settings()

    def _minimal_env(self, workdir: str) -> dict:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "HOME": workdir,
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _run(self, cmd: list, cwd: str, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=self._minimal_env(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def verify(self, patch_text: Optional[str] = None) -> VerificationOutcome:
        if not self.settings.verification_enabled:
            return VerificationOutcome(status="UNABLE_TO_VERIFY", details="Verification is disabled (VERIFICATION_ENABLED=false).")

        workdir = tempfile.mkdtemp(prefix="agentic_review_verify_")
        try:
            clone = self._run(
                ["git", "clone", "--quiet", "--no-tags", self.clone_url, "repo"],
                cwd=workdir,
                timeout=self.settings.verification_timeout_seconds,
            )
            if clone.returncode != 0:
                return VerificationOutcome(
                    status="UNABLE_TO_VERIFY",
                    details=f"git clone failed: {clone.stderr[-2000:]}",
                )
            repo_dir = os.path.join(workdir, "repo")

            checkout = self._run(
                ["git", "checkout", "--quiet", self.commit_sha],
                cwd=repo_dir,
                timeout=self.settings.verification_timeout_seconds,
            )
            if checkout.returncode != 0:
                return VerificationOutcome(
                    status="UNABLE_TO_VERIFY",
                    details=f"git checkout {self.commit_sha} failed: {checkout.stderr[-2000:]}",
                )

            patch_applied = not patch_text  # no patch to apply => trivially "applied" (testing as-is)
            if patch_text:
                patch_path = os.path.join(workdir, "fix.patch")
                with open(patch_path, "w", encoding="utf-8") as fh:
                    fh.write(patch_text)
                apply_res = self._run(
                    ["git", "apply", "--whitespace=nowarn", patch_path],
                    cwd=repo_dir,
                    timeout=30,
                )
                if apply_res.returncode != 0:
                    return VerificationOutcome(
                        status="UNABLE_TO_VERIFY",
                        details=f"Proposed patch did not apply cleanly: {apply_res.stderr[-2000:]}",
                        patch_applied=False,
                    )
                patch_applied = True

            outcome = self._run_tests(repo_dir)
            outcome.patch_applied = patch_applied
            return outcome
        except subprocess.TimeoutExpired as e:
            return VerificationOutcome(status="UNABLE_TO_VERIFY", details=f"Timed out: {e}")
        except FileNotFoundError as e:
            return VerificationOutcome(status="UNABLE_TO_VERIFY", details=f"Required tool not found: {e}")
        except Exception as e:
            logger.exception("verification_unexpected_error")
            return VerificationOutcome(status="UNABLE_TO_VERIFY", details=f"Unexpected verification error: {e}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _run_tests(self, repo_dir: str) -> VerificationOutcome:
        allowed = get_allowed_test_commands()
        has_setup = any(
            os.path.exists(os.path.join(repo_dir, f))
            for f in ("pytest.ini", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        )
        if not has_setup:
            return VerificationOutcome(
                status="UNABLE_TO_VERIFY",
                details="No recognizable Python test/project setup found in this repository; skipping execution.",
            )

        for cmd_str in allowed:
            cmd = cmd_str.split()
            try:
                result = self._run(cmd, cwd=repo_dir, timeout=self.settings.verification_timeout_seconds)
            except FileNotFoundError:
                continue  # this particular test runner isn't installed; try the next allowlisted one

            output = (result.stdout or "") + "\n" + (result.stderr or "")

            if result.returncode != 0 and _looks_like_missing_runner(output):
                # e.g. "No module named pytest" -- the runner itself isn't
                # available rather than the tests having failed. Try the
                # next allowlisted command instead of reporting FAILED.
                continue

            passed, failed = _parse_pytest_summary(output)

            if result.returncode == 0:
                return VerificationOutcome(
                    status="VERIFIED",
                    tests_passed=passed,
                    tests_failed=failed,
                    details=output[-4000:],
                    command_run=cmd_str,
                )
            elif passed or failed:
                status = "PARTIALLY_VERIFIED" if passed > 0 and failed > 0 else "FAILED"
                return VerificationOutcome(
                    status=status,
                    tests_passed=passed,
                    tests_failed=failed,
                    details=output[-4000:],
                    command_run=cmd_str,
                )
            else:
                return VerificationOutcome(
                    status="FAILED",
                    details=output[-4000:],
                    command_run=cmd_str,
                )

        return VerificationOutcome(
            status="UNABLE_TO_VERIFY",
            details="None of the allowlisted test commands were available in this environment.",
        )


def _looks_like_missing_runner(output: str) -> bool:
    lowered = output.lower()
    return "no module named" in lowered or "command not found" in lowered or "is not recognized" in lowered


def _parse_pytest_summary(output: str) -> tuple:
    """Best-effort parse of a test runner's trailing summary. Supports
    both pytest's summary line ('3 passed, 1 failed in 0.42s') and
    unittest's ('Ran 4 tests ...' + 'OK' / 'FAILED (failures=1, errors=2)').
    Falls back to (0, 0) if neither pattern is found -- the raw output is
    preserved in `details` either way, so nothing is lost, just uncounted."""
    import re

    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    if passed_match or failed_match:
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        return passed, failed

    # unittest-style output
    ran_match = re.search(r"Ran (\d+) tests?", output)
    if ran_match:
        total = int(ran_match.group(1))
        fail_count = 0
        failures_match = re.search(r"failures=(\d+)", output)
        errors_match = re.search(r"errors=(\d+)", output)
        if failures_match:
            fail_count += int(failures_match.group(1))
        if errors_match:
            fail_count += int(errors_match.group(1))
        if fail_count == 0 and re.search(r"\bFAILED\b", output):
            fail_count = 1  # failed but couldn't parse an exact count
        passed = max(total - fail_count, 0)
        return passed, fail_count

    return 0, 0
