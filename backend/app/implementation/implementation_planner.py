"""
implementation_planner -- normalizes the `implementation_plan` object
returned by the ImplementationAgent (agents/implementation_agent.py) into
a validated, consistently-shaped Plan, filling in safe defaults for any
optional field the model omitted. The agent (backed by
llm/prompts/implementation_prompt.py) is what actually *produces* the
plan by reasoning over real repository context -- this module's job is
strictly normalization/validation, not independent plan generation, so
the plan the API returns always has a predictable shape regardless of
minor variance in the model's JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImplementationPlan:
    objective: str = ""
    assumptions: list = field(default_factory=list)
    files_to_modify: list = field(default_factory=list)
    files_to_create: list = field(default_factory=list)
    symbols_to_modify: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    tests: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    expected_behavior: str = ""

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "assumptions": self.assumptions,
            "files_to_modify": self.files_to_modify,
            "files_to_create": self.files_to_create,
            "symbols_to_modify": self.symbols_to_modify,
            "steps": self.steps,
            "tests": self.tests,
            "risks": self.risks,
            "expected_behavior": self.expected_behavior,
        }


def _as_str_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


def build_plan(raw_plan: dict, tests_to_add: Optional[list] = None, agent_risks: Optional[list] = None) -> ImplementationPlan:
    """`raw_plan` is the `implementation_plan` dict from the agent's
    structured response. `tests_to_add`/`agent_risks` are the sibling
    top-level fields from that same response, merged in here since a
    plan is incomplete without knowing what tests/risks accompany it."""
    raw_plan = raw_plan or {}
    return ImplementationPlan(
        objective=str(raw_plan.get("objective") or ""),
        assumptions=_as_str_list(raw_plan.get("assumptions")),
        files_to_modify=_as_str_list(raw_plan.get("files_to_modify")),
        files_to_create=_as_str_list(raw_plan.get("files_to_create")),
        symbols_to_modify=_as_str_list(raw_plan.get("symbols_to_modify")),
        steps=_as_str_list(raw_plan.get("steps")),
        tests=_as_str_list(raw_plan.get("tests") or tests_to_add),
        risks=_as_str_list(raw_plan.get("risks") or agent_risks),
        expected_behavior=str(raw_plan.get("expected_behavior") or ""),
    )
