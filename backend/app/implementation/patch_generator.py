"""
patch_generator -- assembles the final unified diff for an implementation
request: the ImplementationAgent's own model-authored patch (covering
modified files) plus deterministically-built diffs (diff_builder.py, not
the LLM) for any `files_to_create` entry that the model's patch text
didn't already cover -- so a brand-new file is never lost just because
the model expressed it as structured content instead of a diff hunk.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .diff_builder import build_new_file_diff, combine_diffs, validate_unified_diff


@dataclass
class GeneratedPatch:
    patch_text: str
    is_syntactically_valid: bool
    validation_errors: list = field(default_factory=list)
    files_touched: list = field(default_factory=list)
    new_files_added_deterministically: list = field(default_factory=list)


def generate_patch(model_patch_text: str, files_to_create: list) -> GeneratedPatch:
    """`files_to_create` is the list of {"path", "content", "reason"}
    dicts from the agent's structured response."""
    model_patch_text = model_patch_text or ""
    model_validation = validate_unified_diff(model_patch_text)
    covered_paths = set(model_validation.file_paths)

    extra_diffs = []
    added_deterministically = []
    for entry in files_to_create or []:
        path = (entry or {}).get("path")
        content = (entry or {}).get("content")
        if not path or content is None:
            continue
        if path in covered_paths:
            continue  # model's patch already includes a hunk for this new file
        extra_diffs.append(build_new_file_diff(path, content))
        added_deterministically.append(path)

    combined = combine_diffs(model_patch_text, *extra_diffs)
    final_validation = validate_unified_diff(combined)

    return GeneratedPatch(
        patch_text=combined,
        is_syntactically_valid=final_validation.valid,
        validation_errors=final_validation.errors,
        files_touched=sorted(set(final_validation.file_paths)),
        new_files_added_deterministically=added_deterministically,
    )
