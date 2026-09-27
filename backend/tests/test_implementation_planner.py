from app.implementation.implementation_planner import build_plan


def test_build_plan_normalizes_full_object():
    raw = {
        "objective": "Add JWT authentication",
        "assumptions": ["users table already exists"],
        "files_to_modify": ["backend/app/api/users.py"],
        "files_to_create": ["backend/app/auth/jwt_utils.py"],
        "symbols_to_modify": ["backend/app/api/users.py::get_user"],
        "steps": ["Add jwt_utils.py", "Wire dependency into get_user"],
        "risks": ["Existing sessions will be invalidated"],
        "expected_behavior": "Unauthenticated requests get HTTP 401",
    }
    plan = build_plan(raw)
    assert plan.objective == "Add JWT authentication"
    assert plan.files_to_modify == ["backend/app/api/users.py"]
    assert plan.expected_behavior == "Unauthenticated requests get HTTP 401"


def test_build_plan_fills_defaults_for_missing_fields():
    plan = build_plan({})
    assert plan.objective == ""
    assert plan.assumptions == []
    assert plan.files_to_modify == []


def test_build_plan_merges_sibling_tests_and_risks():
    plan = build_plan({"objective": "x"}, tests_to_add=["test the new endpoint"], agent_risks=["breaking change"])
    assert plan.tests == ["test the new endpoint"]
    assert plan.risks == ["breaking change"]


def test_build_plan_handles_none():
    plan = build_plan(None)
    assert plan.objective == ""
