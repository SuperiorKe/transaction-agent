"""LLM provider interface. The negotiation engine only ever sees these types.

Adapters (e.g. `app.llm.anthropic`) translate them to and from a vendor SDK.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


Part = TextPart | ToolCall | ToolResult


@dataclass(frozen=True)
class Message:
    role: Role
    parts: tuple[Part, ...]
    # Opaque vendor content for assistant turns, echoed back verbatim on the next request so
    # vendor-only blocks (e.g. thinking) survive. The engine never inspects it.
    provider_payload: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    parts: tuple[TextPart | ToolCall, ...]
    stop_reason: StopReason
    provider_payload: Any = None

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(p for p in self.parts if isinstance(p, ToolCall))


class LLMError(Exception):
    """A provider call failed. `retryable` distinguishes transient failures from bad config."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(Protocol):
    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 16000,
    ) -> LLMResponse: ...
