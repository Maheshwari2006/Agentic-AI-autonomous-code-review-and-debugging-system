"""
ImplementationAgent -- the agentic core of the code-fetching +
implementation-suggestion feature.

Follows the exact same tool-loop pattern as agents/review_agent.py's
ReviewAgent: send the initial (file-discovery-built) context plus a set
of repository-browsing tools, let Claude decide what else it needs to
read, execute those tool calls locally/lazily against the real
repository, and repeat until it returns its final structured JSON answer
or the iteration safety cap (config.max_agent_tool_iterations) is hit.
"""
from __future__ import annotations

import json

from .implementation_tools import TOOL_DEFINITIONS_IMPLEMENTATION, ImplementationToolExecutor
from ..config import get_settings
from ..implementation.context import ImplementationContext
from ..llm.provider import LLMProvider
from ..llm.json_utils import parse_json_response
from ..llm.prompts.implementation_prompt import IMPLEMENTATION_SYSTEM_PROMPT
from ..logging_config import get_logger

logger = get_logger(__name__)

_REQUIRED_TOP_LEVEL_KEYS = {
    "summary", "implementation_plan", "suggestions", "files_to_create",
    "patch", "tests_to_add", "verification_requirements", "risks",
}


def _format_initial_message(ctx: ImplementationContext) -> str:
    bundle = ctx.context_bundle
    parts = [
        f"Implementation request: {ctx.request_text}",
        "",
        f"Repository: {ctx.owner}/{ctx.repo} at {bundle['repository']['ref']} "
        f"(resolved commit {bundle['repository']['resolved_sha'][:10]})",
    ]
    if ctx.target_file:
        parts.append(f"Explicit target file: {ctx.target_file}")
    if ctx.target_symbol:
        parts.append(f"Explicit target symbol: {ctx.target_symbol}")

    parts.append("")
    parts.append(f"--- {len(bundle['files'])} relevant file(s) identified by file discovery ---")
    for f in bundle["files"]:
        parts.append(f"\n## {f['path']} ({f['language']})")
        if f["relevance_reasons"]:
            parts.append(f"Relevance: {'; '.join(f['relevance_reasons'])}")
        if f["symbols"]:
            names = ", ".join(s["qualified_name"] for s in f["symbols"][:30])
            parts.append(f"Symbols: {names}")
        parts.append("```\n" + f["content"] + "\n```")

    if bundle["dependency_context"]:
        parts.append("\n--- Dependency context (callers/callees of matched symbols) ---")
        for key, dep in bundle["dependency_context"].items():
            parts.append(f"\n### {key}")
            for c in dep["callers"]:
                parts.append(f"Caller in {c['file']} ({c['name']}):\n```\n{c['source']}\n```")
            for c in dep["callees"]:
                parts.append(f"Callee in {c['file']} ({c['name']}):\n```\n{c['source']}\n```")

    if bundle["related_tests"]:
        parts.append("\n--- Related existing tests ---")
        for path, content in bundle["related_tests"].items():
            parts.append(f"\n## {path}\n```\n{content}\n```")

    if bundle["config_files"]:
        parts.append("\n--- Configuration files ---")
        for path, content in bundle["config_files"].items():
            parts.append(f"\n## {path}\n```\n{content}\n```")

    parts.append(
        "\nYou have tools to read further files/functions in this repository if this context is "
        "not sufficient. When ready, respond with the final JSON object as instructed."
    )
    return "\n".join(parts)


def _validate_response(parsed) -> str:
    """Returns an error string, or '' if the response shape is usable."""
    if not isinstance(parsed, dict):
        return "Model response was valid JSON but not an object."
    missing = _REQUIRED_TOP_LEVEL_KEYS - set(parsed.keys())
    if missing:
        return f"Model response is missing required key(s): {sorted(missing)}"
    if not isinstance(parsed.get("implementation_plan"), dict):
        return "'implementation_plan' must be an object."
    if not isinstance(parsed.get("suggestions"), list):
        return "'suggestions' must be a list."
    if not isinstance(parsed.get("patch"), str):
        return "'patch' must be a string (unified diff text, possibly empty)."
    return ""


class ImplementationAgent:
    name = "implementation_agent"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, ctx: ImplementationContext) -> dict:
        settings = get_settings()
        executor = ImplementationToolExecutor(ctx)

        messages = [{"role": "user", "content": _format_initial_message(ctx)}]

        result = None
        parse_error = None

        for iteration in range(1, settings.max_agent_tool_iterations + 1):
            response = self.llm.complete(
                system=IMPLEMENTATION_SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_DEFINITIONS_IMPLEMENTATION,
                max_tokens=settings.anthropic_max_tokens,
            )

            if response.tool_calls:
                assistant_content = []
                if response.text:
                    assistant_content.append({"type": "text", "text": response.text})
                for tc in response.tool_calls:
                    assistant_content.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                    )
                messages.append({"role": "assistant", "content": assistant_content})

                tool_result_content = []
                for tc in response.tool_calls:
                    ctx.log(self.name, f"tool_call {tc.name}({tc.input})")
                    tool_result = executor.execute(tc.name, tc.input)
                    tool_result_content.append(
                        {"type": "tool_result", "tool_use_id": tc.id, "content": _safe_json(tool_result)}
                    )
                messages.append({"role": "user", "content": tool_result_content})
                continue

            parsed, err = parse_json_response(response.text)
            if err:
                parse_error = err
            else:
                shape_err = _validate_response(parsed)
                if shape_err:
                    parse_error = shape_err
                else:
                    result = parsed
            break
        else:
            parse_error = (
                f"Implementation agent hit the {settings.max_agent_tool_iterations}-iteration "
                "tool-call safety cap without a final answer."
            )

        if parse_error:
            ctx.log(self.name, f"implementation_response_issue: {parse_error}", level="error")
            raise RuntimeError(f"Implementation agent could not produce a valid response: {parse_error}")

        ctx.plan = result["implementation_plan"]
        ctx.suggestions = result["suggestions"]
        ctx.patch_text = result.get("patch") or ""
        ctx.files_to_create = result.get("files_to_create") or []
        ctx.tests_to_add = result.get("tests_to_add") or []
        ctx.risks = result.get("risks") or []
        ctx.llm_summary = result.get("summary", "")

        ctx.log(self.name, f"Implementation analysis complete: {len(ctx.suggestions)} suggestion(s).")
        return result


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj)[:200000]
    except TypeError:
        return json.dumps(str(obj))
