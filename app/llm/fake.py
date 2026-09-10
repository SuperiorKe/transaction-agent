"""Scripted LLM provider for offline tests: returns queued responses and records requests."""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.llm.base import LLMError, LLMResponse, Message, TextPart, ToolCall, ToolSpec


@dataclass(frozen=True)
class GenerateRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int


class ScriptedLLMProvider:
    def __init__(self, script: Iterable[LLMResponse | LLMError]) -> None:
        self._script = deque(script)
        self.requests: list[GenerateRequest] = []

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 16000,
    ) -> LLMResponse:
        self.requests.append(GenerateRequest(system, tuple(messages), tuple(tools), max_tokens))
        if not self._script:
            raise AssertionError("ScriptedLLMProvider ran out of scripted responses")
        item = self._script.popleft()
        if isinstance(item, LLMError):
            raise item
        return item


def reply(text: str) -> LLMResponse:
    return LLMResponse(parts=(TextPart(text),), stop_reason="end_turn")


def tool_use(*calls: ToolCall, text: str = "") -> LLMResponse:
    parts: tuple[TextPart | ToolCall, ...] = ((TextPart(text),) if text else ()) + calls
    return LLMResponse(parts=parts, stop_reason="tool_use")
