"""Agent 4 -- Code Review Agent.

This is the agentic core of the system: rather than `diff -> LLM ->
response`, Claude is given a set of tools (see agents/tools.py) and a
summary of what changed, and decides for itself what additional context
it needs -- reading a function at its previous revision, finding callers,
pulling in a related test file -- before producing a structured verdict.

The loop:
  1. Send system prompt + initial change summary + tool definitions.
  2. If Claude responds with tool_use blocks, execute them locally
     (fast: everything the tools touch was already fetched/indexed by
     the Repository Explorer and Dependency Analyzer agents) and send
     tool_result blocks back.
  3. Repeat until Claude stops calling tools or a max-iteration safety
     cap is hit (config.max_agent_tool_iterations -- a loop-safety bound,
     not a content-size limit).
  4. Parse the final text response as the structured issues JSON.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from .tools import TOOL_DEFINITIONS, ToolExecutor
from ..config import get_settings
from ..llm.provider import LLMProvider
from ..llm.json_utils import parse_json_response
from ..logging_config import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an autonomous senior code reviewer and debugger analyzing a single \
Git commit. You have tools available to inspect the repository -- use them whenever you need \
more context than what's already been given (e.g. to see a caller of a changed function, the \
previous version of a function you don't have yet, or a related test file). Do not guess about \
code you have not read.

Review the changed code for issues in these categories: CORRECTNESS, SECURITY, PERFORMANCE, \
MAINTAINABILITY, RELIABILITY, TESTING, API_COMPATIBILITY.

For each real issue you find, decide a severity: CRITICAL, HIGH, MEDIUM, LOW, or INFO.

When you are done investigating, respond with ONLY a single JSON array (no prose, no markdown \
fences) of issue objects, each shaped exactly like this:
{
  "file": "path/to/file.py",
  "function": "qualified_name or null",
  "line": 42,
  "severity": "HIGH",
  "category": "SECURITY",
  "title": "short title",
  "description": "what the issue is",
  "evidence": "the specific code/behavior that shows this is a problem",
  "impact": "what could go wrong as a result",
  "confidence": 0.0 to 1.0
}
If you find no issues, respond with an empty JSON array: []
Do not fabricate line numbers or code you have not actually read via the provided context or tools."""


def _format_change_summary(ctx: AnalysisContext) -> str:
    parts = [f"Commit message: {ctx.commit.message.strip()}", ""]
    for gi, group in enumerate(ctx.change_groups, start=1):
        parts.append(f"--- Change group {gi} ({len(group)} symbol(s)) ---")
        for c in group:
            parts.append(f"\n[{c.change_type.upper()}] {c.symbol_type} `{c.qualified_name}` in {c.file_path}")
            if c.previous_symbol:
                parts.append("Previous implementation:\n```\n" + c.previous_symbol.source + "\n```")
            if c.new_symbol:
                parts.append("New implementation:\n```\n" + c.new_symbol.source + "\n```")
    return "\n".join(parts)


class ReviewAgent(Agent):
    name = "code_reviewer"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        settings = get_settings()
        executor = ToolExecutor(ctx)

        messages = [{"role": "user", "content": _format_change_summary(ctx)}]

        final_issues = []
        parse_error = None

        for iteration in range(1, settings.max_agent_tool_iterations + 1):
            response = self.llm.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                max_tokens=get_settings().anthropic_max_tokens,
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
                    result = executor.execute(tc.name, tc.input)
                    tool_result_content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": _safe_json(result),
                        }
                    )
                messages.append({"role": "user", "content": tool_result_content})
                continue

            # No more tool calls: this should be the final structured answer.
            parsed, err = parse_json_response(response.text)
            if err:
                parse_error = err
                final_issues = []
            elif isinstance(parsed, list):
                final_issues = parsed
            else:
                final_issues = []
                parse_error = "Model response was valid JSON but not a list of issues."
            break
        else:
            parse_error = f"Review agent hit the {settings.max_agent_tool_iterations}-iteration tool-call safety cap without a final answer."

        if parse_error:
            ctx.log(self.name, f"review_parse_issue: {parse_error}", level="warning")

        ctx.issues.extend(final_issues)
        ctx.log(self.name, f"Code review complete: {len(final_issues)} issue(s) found.")
        return ctx


def _safe_json(obj) -> str:
    import json

    try:
        return json.dumps(obj)[:200000]  # sanity cap against a runaway tool result, not a review limit
    except TypeError:
        return json.dumps(str(obj))
