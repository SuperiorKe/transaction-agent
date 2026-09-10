"""AnthropicLLMProvider: offline tests against a stubbed SDK client, plus opt-in live tests."""

import os
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage

from app.config import Settings
from app.llm.anthropic import REFUSAL_FALLBACK_BETA, AnthropicLLMProvider
from app.llm.base import LLMError, Message, TextPart, ToolCall, ToolResult, ToolSpec

SPEC = ToolSpec(
    "record_offer",
    "Record the provider's stated price.",
    {"type": "object", "properties": {"amount": {"type": "integer"}}, "required": ["amount"]},
)
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def sdk_message(content: list[dict], stop_reason: str = "end_turn") -> BetaMessage:
    return BetaMessage.construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-opus-5",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


class StubEndpoint:
    def __init__(self, result=None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._result, self._error = result, error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._result

    async def retrieve(self, model_id):
        self.calls.append({"model_id": model_id})
        if self._error:
            raise self._error
        return SimpleNamespace(id=model_id, display_name="Claude Opus 5")


def stub_client(result=None, error=None) -> SimpleNamespace:
    return SimpleNamespace(
        messages=StubEndpoint(result, error),
        beta=SimpleNamespace(messages=StubEndpoint(result, error)),
        models=StubEndpoint(error=error),
    )


def provider(client, **kwargs) -> AnthropicLLMProvider:
    return AnthropicLLMProvider(model="claude-opus-5", client=client, **kwargs)


HISTORY = (
    Message("user", (TextPart("I charge 23,000"),)),
    Message("assistant", (ToolCall("tu_1", "record_offer", {"amount": 23000}),)),
    Message("user", (ToolResult("tu_1", "amount not heard", is_error=True),)),
)


async def test_request_shape_with_defaults():
    client = stub_client(sdk_message([{"type": "text", "text": "ok"}]))

    await provider(client).generate(
        system="SYSTEM", messages=HISTORY, tools=(SPEC,), max_tokens=2048
    )

    (call,) = client.beta.messages.calls
    assert call["model"] == "claude-opus-5"
    assert call["max_tokens"] == 2048
    assert call["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
    ]
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == [REFUSAL_FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert call["tools"] == [
        {"name": "record_offer", "description": SPEC.description, "input_schema": SPEC.input_schema}
    ]
    assert call["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "I charge 23,000"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "record_offer",
                    "input": {"amount": 23000},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": "amount not heard",
                    "is_error": True,
                }
            ],
        },
    ]
    assert client.messages.calls == []


async def test_fallback_and_effort_can_be_disabled():
    client = stub_client(sdk_message([{"type": "text", "text": "ok"}]))

    await provider(client, effort="", refusal_fallback="").generate(
        system="S", messages=HISTORY[:1]
    )

    (call,) = client.messages.calls
    assert "betas" not in call and "fallbacks" not in call and "output_config" not in call
    assert "tools" not in call


async def test_response_maps_text_tool_calls_and_keeps_payload():
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Let me note that."},
        {"type": "tool_use", "id": "tu_9", "name": "record_offer", "input": {"amount": 21000}},
    ]
    client = stub_client(sdk_message(content, stop_reason="tool_use"))

    result = await provider(client).generate(system="S", messages=HISTORY[:1], tools=(SPEC,))

    assert result.stop_reason == "tool_use"
    assert result.text == "Let me note that."
    assert result.tool_calls == (ToolCall("tu_9", "record_offer", {"amount": 21000}),)
    assert [block["type"] for block in result.provider_payload] == ["thinking", "text", "tool_use"]


async def test_assistant_payload_is_echoed_verbatim():
    payload = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Hi"},
    ]
    client = stub_client(sdk_message([{"type": "text", "text": "ok"}]))
    history = (
        HISTORY[0],
        Message("assistant", (TextPart("Hi"),), payload),
        Message("user", (TextPart("Yes"),)),
    )

    await provider(client).generate(system="S", messages=history)

    assert client.beta.messages.calls[0]["messages"][1] == {"role": "assistant", "content": payload}


@pytest.mark.parametrize(
    ("sdk_stop", "expected"),
    [("end_turn", "end_turn"), ("stop_sequence", "end_turn"), ("max_tokens", "max_tokens"),
     ("refusal", "refusal"), ("pause_turn", "other")],
)  # fmt: skip
async def test_stop_reason_mapping(sdk_stop, expected):
    client = stub_client(sdk_message([], stop_reason=sdk_stop))
    result = await provider(client).generate(system="S", messages=HISTORY[:1])
    assert result.stop_reason == expected


def status_error(cls, status: int):
    return cls("boom", response=httpx2.Response(status, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (status_error(anthropic.RateLimitError, 429), True),
        (status_error(anthropic.InternalServerError, 500), True),
        (status_error(anthropic.APIStatusError, 529), True),
        (anthropic.APIConnectionError(request=REQUEST), True),
        (anthropic.APITimeoutError(request=REQUEST), True),
        (status_error(anthropic.AuthenticationError, 401), False),
        (status_error(anthropic.NotFoundError, 404), False),
        (status_error(anthropic.BadRequestError, 400), False),
    ],
)
async def test_sdk_errors_become_llm_errors(error, retryable):
    with pytest.raises(LLMError) as exc_info:
        await provider(stub_client(error=error)).generate(system="S", messages=HISTORY[:1])
    assert exc_info.value.retryable is retryable


def test_model_is_required():
    with pytest.raises(ValueError, match="ANTHROPIC_MODEL"):
        AnthropicLLMProvider(model="", client=stub_client())


async def test_describe_model_reports_display_name():
    assert await provider(stub_client()).describe_model() == "Claude Opus 5"


async def test_describe_model_unknown_model_is_not_retryable():
    client = stub_client(error=status_error(anthropic.NotFoundError, 404))
    with pytest.raises(LLMError, match="not available") as exc_info:
        await provider(client).describe_model()
    assert exc_info.value.retryable is False


# --- live: real Anthropic API, opt in with `uv run pytest -m live` -----------------------------


def live_provider() -> AnthropicLLMProvider:
    settings = Settings()
    if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")):
        pytest.skip("ANTHROPIC_API_KEY not set")
    return AnthropicLLMProvider(model=settings.anthropic_model, api_key=settings.anthropic_api_key)


@pytest.mark.live
async def test_live_configured_model_is_available():
    assert await live_provider().describe_model()


@pytest.mark.live
async def test_live_tool_use_round_trip():
    llm = live_provider()
    system = (
        "When the user states a price, call record_offer with it, "
        "then reply with one short sentence."
    )
    first = Message("user", (TextPart("The price is 23,000 shillings for six hours."),))

    response = await llm.generate(system=system, messages=[first], tools=(SPEC,), max_tokens=4096)

    assert response.stop_reason == "tool_use"
    (call,) = response.tool_calls
    assert call.name == "record_offer" and call.arguments == {"amount": 23000}

    followup = await llm.generate(
        system=system,
        messages=[
            first,
            Message("assistant", response.parts, response.provider_payload),
            Message("user", (ToolResult(call.id, '{"ok": true}'),)),
        ],
        tools=(SPEC,),
        max_tokens=4096,
    )
    assert followup.stop_reason == "end_turn"
    assert followup.text.strip()
