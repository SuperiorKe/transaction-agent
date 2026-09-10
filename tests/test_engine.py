from collections.abc import Sequence

from app.agent.engine import (
    CALL_CONNECTED,
    FORCED_GOODBYE,
    REPEAT_PLEASE,
    NegotiationEngine,
    ToolOutcome,
)
from app.conversation import AgentTurn, CallerMessage
from app.llm.base import LLMError, LLMResponse, TextPart, ToolCall, ToolResult, ToolSpec
from app.llm.fake import ScriptedLLMProvider, reply, tool_use

SPEC = ToolSpec("record_offer", "Record the provider's offer.", {"type": "object"})


class RecordingTools:
    def __init__(self, outcomes: dict[str, ToolOutcome] | None = None, fail: bool = False):
        self.calls: list[ToolCall] = []
        self._outcomes = outcomes or {}
        self._fail = fail

    def specs(self) -> Sequence[ToolSpec]:
        return (SPEC,)

    async def execute(self, call: ToolCall) -> ToolOutcome:
        self.calls.append(call)
        if self._fail:
            raise RuntimeError("database is locked")
        return self._outcomes.get(call.name, ToolOutcome('{"ok": true}'))


def engine(script, tools=None, **kwargs) -> tuple[NegotiationEngine, ScriptedLLMProvider]:
    llm = ScriptedLLMProvider(script)
    return NegotiationEngine(
        llm, system_prompt="SYSTEM", tools=tools or RecordingTools(), **kwargs
    ), llm


async def test_start_sends_call_connected_marker_and_speaks():
    agent, llm = engine([reply("Hi, I'm an AI assistant calling for a client.")])

    turn = await agent.start()

    assert turn == AgentTurn("Hi, I'm an AI assistant calling for a client.")
    request = llm.requests[0]
    assert request.system == "SYSTEM"
    assert request.tools == (SPEC,)
    assert request.messages[0].parts == (TextPart(CALL_CONNECTED),)


async def test_tool_round_then_reply_feeds_results_back():
    call = ToolCall("tu_1", "record_offer", {"amount": 23000})
    tools = RecordingTools()
    agent, llm = engine([tool_use(call), reply("The client is working with KES 20,000.")], tools)

    turn = await agent.respond(CallerMessage("I charge 23,000"))

    assert turn.say == "The client is working with KES 20,000."
    assert tools.calls == [call]
    second = llm.requests[1].messages
    assert [m.role for m in second] == ["user", "assistant", "user"]
    assert second[1].parts == (call,)
    assert second[2].parts == (ToolResult("tu_1", '{"ok": true}'),)


async def test_all_tool_results_from_one_turn_go_back_in_one_message():
    calls = (ToolCall("a", "record_offer", {}), ToolCall("b", "record_offer", {}))
    agent, llm = engine([tool_use(*calls), reply("ok")])

    await agent.respond(CallerMessage("hello"))

    last = llm.requests[1].messages[-1]
    assert last.role == "user"
    assert [r.tool_call_id for r in last.parts] == ["a", "b"]


async def test_tool_requesting_end_call_ends_after_final_words():
    tools = RecordingTools({"end_call": ToolOutcome('{"ok": true}', end_call=True)})
    agent, _ = engine([tool_use(ToolCall("t", "end_call", {})), reply("Goodbye.")], tools)

    assert await agent.respond(CallerMessage("21,000 is final")) == AgentTurn(
        "Goodbye.", end_call=True
    )


async def test_provider_payload_is_kept_on_assistant_history():
    payload = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    response = LLMResponse(
        parts=(TextPart("Hello"),), stop_reason="end_turn", provider_payload=payload
    )
    agent, _ = engine([response])

    await agent.start()

    assert agent.history[-1].provider_payload is payload


async def test_tool_exception_becomes_error_result_not_crash():
    agent, llm = engine(
        [tool_use(ToolCall("t", "record_offer", {})), reply("Sorry.")], RecordingTools(fail=True)
    )

    turn = await agent.respond(CallerMessage("hi"))

    assert turn.say == "Sorry."
    result = llm.requests[1].messages[-1].parts[0]
    assert result.is_error and "database is locked" in result.content


async def test_retryable_llm_error_asks_caller_to_repeat():
    agent, _ = engine([LLMError("overloaded", retryable=True)])

    turn = await agent.respond(CallerMessage("hi"))

    assert turn == AgentTurn(REPEAT_PLEASE, note="llm_error_retryable")


async def test_non_retryable_llm_error_ends_call():
    agent, _ = engine([LLMError("model not found", retryable=False)])

    assert await agent.start() == AgentTurn(FORCED_GOODBYE, end_call=True, note="llm_error")


async def test_refusal_ends_call_without_speaking_model_text():
    refused = LLMResponse(parts=(), stop_reason="refusal")
    agent, _ = engine([refused])

    assert await agent.respond(CallerMessage("hi")) == AgentTurn(
        FORCED_GOODBYE, end_call=True, note="refusal"
    )


async def test_tool_round_limit_stops_runaway_loop():
    looping = [tool_use(ToolCall(f"t{i}", "record_offer", {})) for i in range(3)]
    agent, llm = engine(looping, max_tool_rounds=2)

    turn = await agent.respond(CallerMessage("hi"))

    assert turn == AgentTurn(REPEAT_PLEASE, note="tool_round_limit")
    assert len(llm.requests) == 3
