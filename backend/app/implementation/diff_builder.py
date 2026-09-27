"""
diff_builder -- unified diff construction and structural validation.

Two responsibilities:

1. `build_new_file_diff` deterministically generates a correct unified
   diff for a brand-new file using Python's stdlib `difflib` (not the
   LLM) -- this guarantees new-file diffs are always syntactically valid
   even if the model's own patch text omitted or malformed them.

2. `validate_unified_diff` does a structural sanity check (proper
   `---`/`+++`/`@@` headers, hunks that parse) on diff text -- whether
   model-authored or produced by (1) -- before it's ever handed to `git
   apply` in the verification step. This is a syntax check only; whether
   the patch actually *applies* against real file content is determined
   for real by services/verification_runner.py (git apply in an isolated
   clone), never assumed here.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field


@dataclass
class DiffValidationResult:
    valid: bool
    errors: list = field(default_factory=list)
    file_paths: list = field(default_factory=list)


def build_new_file_diff(path: str, content: str) -> str:
    if not content.endswith("\n"):
        content += "\n"
    new_lines = content.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        [], new_lines, fromfile="/dev/null", tofile=f"b/{path}", lineterm="\n",
    )
    header = [f"--- /dev/null\n", f"+++ b/{path}\n"]
    body = list(diff_lines)[2:]  # difflib already emits the from/to lines; keep ours for consistent a/ b/ prefixes
    return "".join(header + body)


def combine_diffs(*diff_texts: str) -> str:
    parts = [d.strip("\n") for d in diff_texts if d and d.strip()]
    return ("\n".join(parts) + "\n") if parts else ""


_FILE_HEADER_RE = re.compile(r"^--- (.+)\n\+\+\+ (.+)$", re.MULTILINE)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def validate_unified_diff(diff_text: str) -> DiffValidationResult:
    if not diff_text or not diff_text.strip():
        return DiffValidationResult(valid=True, file_paths=[])  # empty patch is valid (no changes)

    errors = []
    file_paths = []

    blocks = re.split(r"(?=^--- )", diff_text, flags=re.MULTILINE)
    blocks = [b for b in blocks if b.strip()]
    if not blocks:
        return DiffValidationResult(valid=False, errors=["No '--- '/'+++ ' file headers found."])

    for block in blocks:
        lines = block.splitlines()
        if not lines or not lines[0].startswith("--- "):
            errors.append("Diff block does not start with '--- '.")
            continue
        if len(lines) < 2 or not lines[1].startswith("+++ "):
            errors.append(f"Missing '+++ ' line after '{lines[0]}'.")
            continue

        to_path = lines[1][4:].strip()
        cleaned = to_path[2:] if to_path.startswith(("a/", "b/")) else to_path
        if cleaned and cleaned != "/dev/null":
            file_paths.append(cleaned)

        has_hunk = any(_HUNK_RE.match(l) for l in lines[2:])
        if not has_hunk:
            errors.append(f"No valid '@@ ... @@' hunk header found for {to_path}.")

    return DiffValidationResult(valid=not errors, errors=errors, file_paths=file_paths)
