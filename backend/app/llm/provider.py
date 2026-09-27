"""
Abstract LLM provider interface. Agents talk to this interface, not to
the Anthropic SDK directly, so a second provider could be added later
(see anthropic_provider.py for the concrete implementation) without
touching agent code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolCallRequest:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list = field(default_factory=list)  # list[ToolCallRequest]
    stop_reason: str = ""
    raw: object = None


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list,
        tools: Optional[list] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send one turn to the model. `messages` follows the Anthropic
        Messages API shape: list of {"role": ..., "content": ...}."""
        raise NotImplementedError
