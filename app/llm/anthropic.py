"""Anthropic Messages API adapter for the LLMProvider interface.

Check that the configured model is available to your key: `uv run python -m app.llm.anthropic`.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import anthropic

from app.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCall,
    ToolResult,
    ToolSpec,
)

log = logging.getLogger(__name__)

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"  # gates `fallbacks: "default"`

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}


def to_anthropic_message(message: Message) -> dict[str, Any]:
    # Echo assistant turns verbatim so thinking/fallback blocks survive the round trip.
    if message.role == "assistant" and message.provider_payload is not None:
        return {"role": "assistant", "content": message.provider_payload}
    content: list[dict[str, Any]] = []
    for part in message.parts:
        match part:
            case TextPart(text=text) if text:
                content.append({"type": "text", "text": text})
            case ToolCall(id=call_id, name=name, arguments=arguments):
                content.append(
                    {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
                )
            case ToolResult(tool_call_id=call_id, content=result, is_error=is_error):
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": result,
                }
                if is_error:
                    block["is_error"] = True
                content.append(block)
    return {"role": message.role, "content": content}


def from_anthropic_response(response: Any) -> LLMResponse:
    parts: list[TextPart | ToolCall] = []
    for block in response.content:
        if block.type == "text":
            parts.append(TextPart(block.text))
        elif block.type == "tool_use":
            parts.append(ToolCall(block.id, block.name, dict(block.input)))
    return LLMResponse(
        parts=tuple(parts),
        stop_reason=_STOP_REASONS.get(response.stop_reason or "", "other"),
        provider_payload=[block.to_dict() for block in response.content],
    )


def to_llm_error(exc: anthropic.AnthropicError) -> LLMError:
    """Most specific first: transient failures are retryable, configuration errors are not."""
    match exc:
        case anthropic.RateLimitError():
            retryable = True
        case anthropic.APIStatusError(status_code=status):
            retryable = status >= 500
        case anthropic.APIConnectionError():  # includes APITimeoutError
            retryable = True
        case _:
            retryable = False
    request_id = getattr(exc, "request_id", None)
    return LLMError(f"{type(exc).__name__}: {exc} (request_id={request_id})", retryable=retryable)


class AnthropicLLMProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        effort: str = "low",
        refusal_fallback: str = "default",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if not model:
            raise ValueError("ANTHROPIC_MODEL must be set")
        self.model = model
        self._effort = effort
        self._refusal_fallback = refusal_fallback
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key or None, timeout=timeout_seconds, max_retries=max_retries
        )

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 16000,
    ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Breakpoint on the static system prompt; tools render before it and cache with it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [to_anthropic_message(m) for m in messages],
        }
        if tools:
            request["tools"] = [to_anthropic_tool(t) for t in tools]
        if self._effort:
            request["output_config"] = {"effort": self._effort}

        try:
            if self._refusal_fallback:
                response = await self._client.beta.messages.create(
                    **request, betas=[REFUSAL_FALLBACK_BETA], fallbacks=self._refusal_fallback
                )
            else:
                response = await self._client.messages.create(**request)
        except anthropic.AnthropicError as exc:
            raise to_llm_error(exc) from exc

        request_id = getattr(response, "_request_id", None)
        log.debug("anthropic response %s stop=%s", request_id, response.stop_reason)
        return from_anthropic_response(response)

    async def describe_model(self) -> str:
        """Confirm the configured model is available to this key; returns its display name."""
        try:
            model = await self._client.models.retrieve(self.model)
        except anthropic.NotFoundError as exc:
            raise LLMError(
                f"model {self.model!r} is not available to this API key", retryable=False
            ) from exc
        except anthropic.AnthropicError as exc:
            raise to_llm_error(exc) from exc
        return model.display_name


async def _check() -> None:
    from app.config import get_settings

    settings = get_settings()
    provider = AnthropicLLMProvider(
        model=settings.anthropic_model, api_key=settings.anthropic_api_key
    )
    print(f"{settings.anthropic_model}: available ({await provider.describe_model()})")


if __name__ == "__main__":
    asyncio.run(_check())
