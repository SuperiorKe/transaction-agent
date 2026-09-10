import json
from collections.abc import Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.engine import NegotiationEngine, ToolOutcome
from app.calls import DIDNT_CATCH, SILENCE_GOODBYE, CallCoordinator
from app.conversation import AgentTurn, CallerMessage
from app.llm.base import ToolCall, ToolSpec
from app.llm.fake import ScriptedLLMProvider, reply, tool_use
from app.middleware import LocalOnlyOwnerRoutes
from app.routes.voice import build_voice_router
from app.telephony.fake import FakeTelephonyProvider, answered, ended, said, silence


class ScriptedAgent:
    def __init__(self) -> None:
        self.started = 0
        self.heard: list[str] = []
        self.finished: list[str | None] = []

    async def start(self) -> AgentTurn:
        self.started += 1
        return AgentTurn("Hi, I'm an AI assistant. Are you available Monday?")

    async def respond(self, message: CallerMessage) -> AgentTurn:
        self.heard.append(message.text)
        return AgentTurn(f"You said: {message.text}")

    async def finish(self, reason: str | None) -> None:
        self.finished.append(reason)


@pytest.fixture
def setup():
    telephony = FakeTelephonyProvider()
    agents: dict[str, ScriptedAgent] = {}

    def factory(session_id: str) -> ScriptedAgent:
        agents[session_id] = ScriptedAgent()
        return agents[session_id]

    return CallCoordinator(telephony, factory), telephony, agents


def spoken(reply_body: str) -> dict:
    return json.loads(reply_body)


async def test_answered_call_opens_with_agent_greeting(setup):
    coordinator, telephony, agents = setup

    result = await coordinator.handle_callback(answered("s1", "+254100000001"))

    assert spoken(result.body) == {
        "say": "Hi, I'm an AI assistant. Are you available Monday?",
        "end_call": False,
    }
    assert agents["s1"].started == 1
    assert coordinator.active_sessions == {"s1"}


async def test_caller_speech_reaches_agent_as_domain_message(setup):
    coordinator, _, agents = setup
    await coordinator.handle_callback(answered("s1"))

    result = await coordinator.handle_callback(said("s1", "  Yes, 23,000 for six hours "))

    assert agents["s1"].heard == ["Yes, 23,000 for six hours"]
    assert spoken(result.body)["say"] == "You said: Yes, 23,000 for six hours"


async def test_repeat_answered_callback_counts_as_silence_not_restart(setup):
    coordinator, _, agents = setup
    await coordinator.handle_callback(answered("s1"))

    again = spoken((await coordinator.handle_callback(answered("s1"))).body)

    assert again == {"say": DIDNT_CATCH, "end_call": False}
    assert agents["s1"].started == 1


async def test_silence_asks_again_then_hangs_up(setup):
    coordinator, _, _ = setup
    await coordinator.handle_callback(answered("s1"))

    first = spoken((await coordinator.handle_callback(silence("s1"))).body)
    second = spoken((await coordinator.handle_callback(silence("s1"))).body)

    assert first == {"say": DIDNT_CATCH, "end_call": False}
    assert second == {"say": SILENCE_GOODBYE, "end_call": True}


async def test_speech_resets_silence_count(setup):
    coordinator, _, _ = setup
    await coordinator.handle_callback(answered("s1"))
    await coordinator.handle_callback(silence("s1"))
    await coordinator.handle_callback(said("s1", "hello?"))

    result = spoken((await coordinator.handle_callback(silence("s1"))).body)

    assert result == {"say": DIDNT_CATCH, "end_call": False}


async def test_empty_transcript_counts_as_silence(setup):
    coordinator, _, agents = setup
    await coordinator.handle_callback(answered("s1"))

    result = spoken((await coordinator.handle_callback(said("s1", "   "))).body)

    assert result["say"] == DIDNT_CATCH
    assert agents["s1"].heard == []


async def test_call_ended_finishes_agent_and_forgets_session(setup):
    coordinator, telephony, agents = setup
    await coordinator.handle_callback(answered("s1"))

    result = await coordinator.handle_callback(ended("s1", "NORMAL_CLEARING"))

    assert result == telephony.acknowledge()
    assert agents["s1"].finished == ["NORMAL_CLEARING"]
    assert coordinator.active_sessions == frozenset()


async def test_ended_for_unknown_session_is_acknowledged(setup):
    coordinator, telephony, _ = setup
    assert await coordinator.handle_callback(ended("ghost")) == telephony.acknowledge()


async def test_speech_before_answered_event_still_reaches_a_new_agent(setup):
    coordinator, _, agents = setup

    await coordinator.handle_callback(said("s2", "Hello, who is this?"))

    assert agents["s2"].heard == ["Hello, who is this?"]


async def test_sessions_are_isolated(setup):
    coordinator, _, agents = setup
    await coordinator.handle_callback(answered("a"))
    await coordinator.handle_callback(answered("b"))
    await coordinator.handle_callback(said("a", "only for a"))

    assert agents["a"].heard == ["only for a"]
    assert agents["b"].heard == []


async def test_fake_provider_records_outbound_calls_and_hangups():
    telephony = FakeTelephonyProvider()

    placed = await telephony.place_call("+254100000001")
    await telephony.hang_up(placed.provider_call_id)

    assert telephony.placed_calls == ["+254100000001"]
    assert telephony.hung_up == ["fake-1"]


class EndCallTools:
    def specs(self) -> Sequence[ToolSpec]:
        return (ToolSpec("end_call", "End the call.", {"type": "object"}),)

    async def execute(self, call: ToolCall) -> ToolOutcome:
        return ToolOutcome('{"ok": true}', end_call=call.name == "end_call")


async def test_full_call_through_fake_phone_and_engine_without_network():
    llm = ScriptedLLMProvider(
        [
            reply("Hi, I'm an AI assistant calling for a client. Are you available Monday?"),
            tool_use(ToolCall("t1", "end_call", {})),
            reply("Thanks. I'll take that to my client. Goodbye."),
        ]
    )
    telephony = FakeTelephonyProvider()
    coordinator = CallCoordinator(
        telephony, lambda _sid: NegotiationEngine(llm, system_prompt="S", tools=EndCallTools())
    )

    greeting = spoken((await coordinator.handle_callback(answered("s1"))).body)
    closing = spoken((await coordinator.handle_callback(said("s1", "No, 21,000 is final."))).body)
    await coordinator.handle_callback(ended("s1"))

    assert greeting["end_call"] is False
    assert closing == {"say": "Thanks. I'll take that to my client. Goodbye.", "end_call": True}
    assert coordinator.active_sessions == frozenset()


@pytest.fixture
def webhook_client(setup):
    coordinator, _, _ = setup
    app = FastAPI()
    app.add_middleware(LocalOnlyOwnerRoutes)
    app.include_router(build_voice_router(coordinator, webhook_secret="s3cret"))
    return TestClient(app)


def test_webhook_with_correct_secret_returns_provider_reply(webhook_client):
    response = webhook_client.post(
        "/webhooks/voice/s3cret",
        data={"event": "answered", "session_id": "s1"},
        headers={"cf-connecting-ip": "203.0.113.7"},  # arrives through the tunnel
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["say"].startswith("Hi, I'm an AI assistant")


def test_webhook_with_wrong_secret_is_404(webhook_client):
    response = webhook_client.post(
        "/webhooks/voice/guess", data={"event": "answered", "session_id": "s1"}
    )
    assert response.status_code == 404


def test_webhook_rejects_everything_when_secret_unconfigured(setup):
    coordinator, _, _ = setup
    app = FastAPI()
    app.include_router(build_voice_router(coordinator, webhook_secret=""))
    response = TestClient(app).post("/webhooks/voice/", data={"event": "answered"})
    assert response.status_code == 404
