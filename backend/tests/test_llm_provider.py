import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_fake_anthropic_module():
    """Install a minimal fake `anthropic` module into sys.modules so
    anthropic_provider.py can be imported and exercised without the real
    SDK installed (this sandbox has no network access for pip install).
    The real project depends on the genuine `anthropic` package at
    runtime -- see requirements.txt."""
    fake = types.ModuleType("anthropic")

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, message="err", status_code=500):
            super().__init__(message)
            self.status_code = status_code
            self.message = message

    class Anthropic:
        def __init__(self, api_key=None, timeout=None):
            self.api_key = api_key
            self.messages = MagicMock()

    fake.RateLimitError = RateLimitError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError
    fake.APIStatusError = APIStatusError
    fake.Anthropic = Anthropic
    sys.modules["anthropic"] = fake
    return fake


@pytest.fixture
def fake_anthropic(monkeypatch):
    return _install_fake_anthropic_module()


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_use_block(id_, name, input_):
    b = MagicMock()
    b.type = "tool_use"
    b.id = id_
    b.name = name
    b.input = input_
    return b


def test_complete_returns_text_response(fake_anthropic):
    from app.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-test", model="claude-test")
    fake_response = MagicMock()
    fake_response.content = [_text_block("hello world")]
    fake_response.stop_reason = "end_turn"
    provider.client.messages.create.return_value = fake_response

    result = provider.complete(system="sys", messages=[{"role": "user", "content": "hi"}])
    assert result.text == "hello world"
    assert result.tool_calls == []


def test_complete_returns_tool_calls(fake_anthropic):
    from app.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-test", model="claude-test")
    fake_response = MagicMock()
    fake_response.content = [_tool_use_block("t1", "read_file", {"path": "auth.py"})]
    fake_response.stop_reason = "tool_use"
    provider.client.messages.create.return_value = fake_response

    result = provider.complete(system="sys", messages=[], tools=[{"name": "read_file"}])
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].input == {"path": "auth.py"}


def test_retries_on_rate_limit_then_succeeds(fake_anthropic, monkeypatch):
    from app.llm.anthropic_provider import AnthropicProvider
    import app.llm.anthropic_provider as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)  # don't actually wait in tests

    provider = AnthropicProvider(api_key="sk-test", model="claude-test")
    ok_response = MagicMock()
    ok_response.content = [_text_block("ok")]
    ok_response.stop_reason = "end_turn"

    provider.client.messages.create.side_effect = [
        fake_anthropic.RateLimitError("rate limited"),
        ok_response,
    ]

    result = provider.complete(system="sys", messages=[])
    assert result.text == "ok"
    assert provider.client.messages.create.call_count == 2


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.llm.anthropic_provider import AnthropicProvider

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(api_key="")
