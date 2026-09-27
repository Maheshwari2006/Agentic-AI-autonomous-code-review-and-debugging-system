"""
AnthropicProvider -- concrete LLMProvider backed by the real Anthropic API.

Handles:
  - retry with exponential backoff on rate limits / transient errors
  - request timeouts
  - tool-use responses (multi-turn agent tool loop)
  - malformed/missing API key errors surfaced as clear RuntimeErrors

The model string is never hard-coded here -- it comes from
settings.anthropic_model (ANTHROPIC_MODEL in .env), so it can be changed
without touching source code.
"""
from __future__ import annotations

import time
from typing import Optional

from ..config import get_settings, require_anthropic_key
from ..logging_config import get_logger
from .provider import LLMProvider, LLMResponse, ToolCallRequest

logger = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or require_anthropic_key()
        self.model = model or settings.anthropic_model
        self.max_retries = settings.llm_max_retries
        self.timeout = settings.llm_timeout_seconds
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "The 'anthropic' package is required. Install it with:\n"
                    "    pip install anthropic"
                ) from e
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
        return self._client

    def complete(
        self,
        system: str,
        messages: list,
        tools: Optional[list] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        import anthropic  # local import: keeps this module importable w/o the SDK installed

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                kwargs = dict(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
                if tools:
                    kwargs["tools"] = tools

                response = self.client.messages.create(**kwargs)
                return self._to_llm_response(response)

            except anthropic.RateLimitError as e:
                wait = min(2 ** attempt, 30)
                logger.warning(f"anthropic_rate_limited attempt={attempt} wait={wait}s")
                time.sleep(wait)
                last_exc = e
            except anthropic.APITimeoutError as e:
                logger.warning(f"anthropic_timeout attempt={attempt}")
                last_exc = e
                time.sleep(min(2 ** attempt, 15))
            except anthropic.APIConnectionError as e:
                logger.warning(f"anthropic_connection_error attempt={attempt} error={e}")
                last_exc = e
                time.sleep(min(2 ** attempt, 15))
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    logger.warning(f"anthropic_server_error attempt={attempt} status={e.status_code}")
                    last_exc = e
                    time.sleep(min(2 ** attempt, 15))
                    continue
                # 4xx (bad request, invalid model, auth) -- not retryable
                raise RuntimeError(f"Anthropic API error {e.status_code}: {e.message}") from e

        raise RuntimeError(
            f"Anthropic API call failed after {self.max_retries} attempts: {last_exc}"
        )

    def _to_llm_response(self, response) -> LLMResponse:
        text_parts = []
        tool_calls = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(ToolCallRequest(id=block.id, name=block.name, input=block.input))
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", ""),
            raw=response,
        )
