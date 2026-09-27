"""Agent 6 -- Fix Generator.

For every issue that survived the Debugging Agent, asks Claude to produce
an implementation-ready fix: the existing code, the suggested code, an
explanation, expected impact, and (where confidence is high enough) a
proper unified diff patch. Patches are never applied automatically -- see
services/verification_runner.py, which only applies them inside an
isolated temporary workspace for testing purposes.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from ..config import get_settings
from ..llm.provider import LLMProvider
from ..llm.json_utils import parse_json_response
from ..logging_config import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a senior engineer writing an implementation-ready fix for a code \
review finding. You will be given the issue and the current source of the affected function/file.

Respond with ONLY a single JSON object:
{
  "explanation": "why this fix addresses the issue",
  "existing_code": "the relevant current code",
  "suggested_code": "the corrected code",
  "unified_diff": "a proper unified diff (---/+++/@@ hunks) transforming existing_code into suggested_code, or empty string if a clean diff isn't meaningful here",
  "expected_impact": "what improves once this is applied",
  "tests_required": "what should be tested to confirm this fix works"
}
PATCH_MIN_CONFIDENCE = 0.55 -- only include a non-empty unified_diff if you are reasonably \
confident the fix is correct and self-contained."""

PATCH_MIN_CONFIDENCE = 0.55


class FixAgent(Agent):
    name = "fix_generator"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def _find_symbol_source(self, ctx: AnalysisContext, file_path: str, function: str) -> str:
        if function:
            for c in ctx.changed_symbols:
                if c.file_path == file_path and (c.symbol_name == function or c.qualified_name == function):
                    if c.new_symbol:
                        return c.new_symbol.source
                    if c.previous_symbol:
                        return c.previous_symbol.source
        return ctx.extra_file_contents.get(file_path, "")

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        settings = get_settings()
        generated = 0

        for issue in ctx.issues:
            source = self._find_symbol_source(ctx, issue.get("file", ""), issue.get("function"))
            prompt = (
                f"Issue:\n{issue}\n\n"
                f"Current source of the affected code:\n```\n{source}\n```\n\n"
                "Produce the fix JSON object now."
            )
            response = self.llm.complete(
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.anthropic_max_tokens,
            )
            parsed, err = parse_json_response(response.text)
            if err or not isinstance(parsed, dict):
                ctx.log(self.name, f"Could not generate fix for '{issue.get('title')}': {err}")
                continue

            issue["suggested_fix_explanation"] = parsed.get("explanation", "")
            issue["suggested_code"] = parsed.get("suggested_code", "")
            issue["existing_code_for_fix"] = parsed.get("existing_code", "")
            issue["expected_impact"] = parsed.get("expected_impact", "")
            issue["tests_required"] = parsed.get("tests_required", "")

            confidence = float(issue.get("confidence", 0.0) or 0.0)
            diff_text = parsed.get("unified_diff", "") or ""
            if confidence >= PATCH_MIN_CONFIDENCE and diff_text.strip():
                issue["suggested_patch"] = diff_text
                generated += 1
            else:
                issue["suggested_patch"] = ""

        ctx.log(self.name, f"Fix generation complete: {generated} patch(es) generated (confidence >= {PATCH_MIN_CONFIDENCE}).")
        return ctx
