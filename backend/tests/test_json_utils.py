from app.llm.json_utils import parse_json_response


def test_parses_clean_json():
    data, err = parse_json_response('{"bug_flagged": true}')
    assert err is None
    assert data["bug_flagged"] is True


def test_strips_markdown_fences():
    text = '```json\n{"a": 1}\n```'
    data, err = parse_json_response(text)
    assert err is None
    assert data == {"a": 1}


def test_strips_fences_without_language_tag():
    text = '```\n[1, 2, 3]\n```'
    data, err = parse_json_response(text)
    assert err is None
    assert data == [1, 2, 3]


def test_extracts_json_from_surrounding_prose():
    text = 'Here is the result:\n{"ok": true}\nHope that helps!'
    data, err = parse_json_response(text)
    assert err is None
    assert data == {"ok": True}


def test_malformed_json_returns_error_not_exception():
    data, err = parse_json_response("not json at all {{{")
    assert data is None
    assert err is not None
