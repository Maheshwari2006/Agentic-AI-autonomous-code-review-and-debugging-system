"""Agent 5 -- Debugging Agent.

Takes the Review Agent's HIGH/CRITICAL findings and re-investigates each
one specifically, with the same tool access, asking Claude to actively
try to disprove the finding (check callers for context that would make it
a non-issue, check whether existing tests already cover it, etc.) before
it's treated as confirmed. Issues that survive this pass get their
evidence/impact refined; issues that don't hold up have their confidence
reduced rather than being silently dropped, so the human reviewer can see
the reasoning either way.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from .tools import TOOL_DEFINITIONS, ToolExecutor
from ..config import get_settings
from ..llm.provider import LLMProvider
from ..llm.json_utils import parse_json_response
from ..logging_config import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a skeptical debugging agent double-checking another AI reviewer's \
finding before it is shown to a human. You have the same repository tools available. \
Try to confirm or refute the finding using real evidence (read the actual code/tests via tools) \
rather than taking it at face value.

Respond with ONLY a single JSON object:
{
  "confirmed": true or false,
  "revised_confidence": 0.0 to 1.0,
  "refined_evidence": "specific evidence for or against, referencing real code you inspected",
  "refined_impact": "what actually happens if this is not fixed"
}"""

INVESTIGATE_THRESHOLD_SEVERITIES = {"CRITICAL", "HIGH"}


class DebuggingAgent(Agent):
    name = "debugging_agent"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        settings = get_settings()
        executor = ToolExecutor(ctx)
        investigated = 0

        for issue in ctx.issues:
            severity = str(issue.get("severity", "")).upper()
            if severity not in INVESTIGATE_THRESHOLD_SEVERITIES:
                continue

            investigated += 1
            prompt = (
                f"Finding to verify:\n{issue}\n\n"
                "Investigate using the available tools, then give your final JSON verdict."
            )
            messages = [{"role": "user", "content": prompt}]
            result = None

            for _ in range(min(6, settings.max_agent_tool_iterations)):
                response = self.llm.complete(
                    system=SYSTEM_PROMPT, messages=messages, tools=TOOL_DEFINITIONS,
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
                        res = executor.execute(tc.name, tc.input)
                        import json

                        tool_result_content.append(
                            {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(res)}
                        )
                    messages.append({"role": "user", "content": tool_result_content})
                    continue

                parsed, err = parse_json_response(response.text)
                if not err and isinstance(parsed, dict):
                    result = parsed
                break

            if result is None:
                ctx.log(self.name, f"Could not verify issue '{issue.get('title')}' (no parseable verdict)")
                continue

            issue["confidence"] = result.get("revised_confidence", issue.get("confidence", 0.5))
            if result.get("refined_evidence"):
                issue["evidence"] = result["refined_evidence"]
            if result.get("refined_impact"):
                issue["impact"] = result["refined_impact"]
            if result.get("confirmed") is False:
                issue["severity"] = "LOW"
                issue["description"] = (
                    issue.get("description", "") + " [Debugging agent could not confirm this as a real issue; severity lowered.]"
                )

        ctx.log(self.name, f"Debugging pass investigated {investigated} high-severity finding(s).")
        return ctx
