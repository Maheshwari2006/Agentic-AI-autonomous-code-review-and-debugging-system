import pytest

from app.agents.implementation_agent import ImplementationAgent
from app.implementation.context import ImplementationContext
from app.github.repository_fetcher import RepositoryHandle
from app.github.tree_parser import ParsedTree
from app.llm.provider import LLMProvider, LLMResponse, ToolCallRequest


class _StubFetcher:
    def get(self, path):
        return None

    def cached_paths(self):
        return []


def _ctx():
    handle = RepositoryHandle(
        owner="o", repo="r", ref="main", resolved_sha="a" * 40,
        default_branch="main", html_url="url", tree=ParsedTree(files=[], directories=[]),
    )
    ctx = ImplementationContext(
        owner="o", repo="r", repo_handle=handle, fetcher=_StubFetcher(),
        request_text="Add JWT authentication to the user API.",
    )
    ctx.context_bundle = {
        "request": ctx.request_text,
        "repository": {"owner": "o", "repo": "r", "ref": "main", "resolved_sha": "a" * 40, "default_branch": "main"},
        "target_file": None, "target_symbol": None,
        "files": [{
            "path": "users.py", "language": "python", "content": "def get_user():\n    return {}\n",
            "symbols": [{"name": "get_user", "qualified_name": "get_user", "symbol_type": "function",
                         "start_line": 1, "end_line": 2, "parent": None}],
            "is_test": False, "is_config": False, "relevance_score": 5.0, "relevance_reasons": ["match"],
        }],
        "dependency_context": {}, "related_tests": {}, "config_files": {},
    }
    return ctx


_VALID_RESPONSE = {
    "summary": "Add JWT auth to the user API.",
    "implementation_plan": {
        "objective": "Add JWT auth", "assumptions": [], "files_to_modify": ["users.py"],
        "files_to_create": [], "symbols_to_modify": ["users.py::get_user"], "steps": ["Add auth check"],
        "risks": [], "expected_behavior": "401 without a token",
    },
    "suggestions": [{
        "file": "users.py", "symbol": "get_user", "location": "inside get_user",
        "change_type": "required", "current_behavior": "no auth", "proposed_change": "add require_auth()",
        "reason": "endpoint is unauthenticated", "expected_behavior": "401 without a token",
    }],
    "files_to_create": [],
    "patch": "--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,3 @@\n def get_user():\n+    require_auth()\n     return {}\n",
    "tests_to_add": ["test unauthenticated request returns 401"],
    "verification_requirements": ["run pytest"],
    "risks": [],
}


class _StubLLM(LLMProvider):
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system, messages, tools=None, max_tokens=4096):
        self.calls += 1
        return self._responses.pop(0)


def test_agent_returns_structured_result_with_no_tool_calls():
    import json

    llm = _StubLLM([LLMResponse(text=json.dumps(_VALID_RESPONSE), tool_calls=[], stop_reason="end_turn")])
    agent = ImplementationAgent(llm)
    ctx = _ctx()
    result = agent.run(ctx)

    assert ctx.plan["objective"] == "Add JWT auth"
    assert len(ctx.suggestions) == 1
    assert "require_auth" in ctx.patch_text
    assert result["summary"] == "Add JWT auth to the user API."


def test_agent_executes_tool_calls_before_final_answer():
    import json

    tool_call_response = LLMResponse(
        text="", tool_calls=[ToolCallRequest(id="t1", name="search_filenames", input={"query": "auth"})],
        stop_reason="tool_use",
    )
    final_response = LLMResponse(text=json.dumps(_VALID_RESPONSE), tool_calls=[], stop_reason="end_turn")
    llm = _StubLLM([tool_call_response, final_response])
    agent = ImplementationAgent(llm)
    ctx = _ctx()

    agent.run(ctx)
    assert llm.calls == 2
    assert any("tool_call search_filenames" in t["message"] for t in ctx.trace)


def test_agent_raises_on_malformed_json():
    llm = _StubLLM([LLMResponse(text="not json at all", tool_calls=[], stop_reason="end_turn")])
    agent = ImplementationAgent(llm)
    with pytest.raises(RuntimeError):
        agent.run(_ctx())


def test_agent_raises_on_missing_required_keys():
    import json

    incomplete = {"summary": "x"}
    llm = _StubLLM([LLMResponse(text=json.dumps(incomplete), tool_calls=[], stop_reason="end_turn")])
    agent = ImplementationAgent(llm)
    with pytest.raises(RuntimeError, match="missing required key"):
        agent.run(_ctx())
