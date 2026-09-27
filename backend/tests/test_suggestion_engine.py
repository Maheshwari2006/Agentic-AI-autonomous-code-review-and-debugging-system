from app.implementation.suggestion_engine import build_suggestions, filter_report


def test_precise_suggestion_is_kept():
    raw = [{
        "file": "backend/app/api/users.py",
        "symbol": "get_user",
        "location": "inside get_user(), before the return statement",
        "change_type": "required",
        "current_behavior": "The endpoint has no authentication dependency.",
        "proposed_change": "Add the existing require_auth dependency to this endpoint.",
        "reason": "The endpoint currently accepts unauthenticated requests.",
        "expected_behavior": "Requests without a valid JWT should return HTTP 401.",
    }]
    result = build_suggestions(raw)
    assert len(result) == 1
    assert result[0].file == "backend/app/api/users.py"
    assert result[0].change_type == "required"


def test_vague_suggestion_is_dropped():
    raw = [{
        "file": "backend/app/api/users.py",
        "proposed_change": "Improve security.",
        "reason": "It is not secure.",
    }]
    result = build_suggestions(raw)
    assert result == []


def test_suggestion_missing_file_is_dropped():
    raw = [{"proposed_change": "Add a real, specific change here", "reason": "a real specific reason here"}]
    assert build_suggestions(raw) == []


def test_invalid_change_type_defaults_to_recommended():
    raw = [{
        "file": "x.py",
        "proposed_change": "Add input validation for the email field",
        "reason": "Malformed emails currently crash the signup handler",
        "change_type": "urgent",
    }]
    result = build_suggestions(raw)
    assert result[0].change_type == "recommended"


def test_filter_report_tracks_dropped_count():
    raw = [
        {
            "file": "x.py",
            "proposed_change": "Add input validation for the email field",
            "reason": "Malformed emails currently crash the signup handler",
        },
        {"file": "y.py", "proposed_change": "Improve performance.", "reason": "It is slow."},
    ]
    report = filter_report(raw)
    assert len(report["suggestions"]) == 1
    assert report["dropped_count"] == 1
