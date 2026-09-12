"""Offline tests for NegotiationCall (epic #10, contract 5). No LLM, no telephony."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.engine import NegotiationEngine, ToolOutcome
from app.agent.session import NegotiationCall
from app.agent.tools import NegotiationToolExecutor
from app.conversation import AgentTurn, CallerMessage
from app.llm.base import ToolCall
from app.llm.fake import ScriptedLLMProvider, reply, tool_use
from app.models import AuditEvent, Negotiation, Offer, Provider, Transaction, TranscriptTurn

ANSWERED = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)


class NoOpTools:
    def specs(self):
        return ()

    async def execute(self, call: ToolCall) -> ToolOutcome:
        raise AssertionError("no tool should be called in this test")


def make_tx(session, **overrides) -> Transaction:
    values = dict(
        request="Photographer for Monday in Nairobi. Maximum KES 20,000.",
        service_date="2026-09-14",
        location="Nairobi",
        max_budget=20000,
        max_attempts=2,
    )
    values.update(overrides)
    tx = Transaction(**values)
    session.add(tx)
    session.flush()
    return tx


def make_negotiation(session, tx, **overrides) -> Negotiation:
    provider = Provider(
        name="Test Photographer", phone="+254100000099", location="Nairobi", priority=1
    )
    session.add(provider)
    session.flush()
    values = dict(
        transaction_id=tx.id,
        provider_id=provider.id,
        kind="negotiation",
        answered_at=ANSWERED.isoformat(timespec="milliseconds"),
    )
    values.update(overrides)
    negotiation = Negotiation(**values)
    session.add(negotiation)
    session.flush()
    return negotiation


def make_negotiation_call(
    session, tx, negotiation, llm, *, tools=None, hard_end_seconds=180, now=None
) -> NegotiationCall:
    engine = NegotiationEngine(llm, system_prompt="SYSTEM", tools=tools or NoOpTools())
    kwargs = {"now": now} if now is not None else {}
    return NegotiationCall(
        session, tx, negotiation, engine, hard_end_seconds=hard_end_seconds, **kwargs
    )


def transcripts(session, negotiation) -> list[TranscriptTurn]:
    return list(
        session.scalars(
            select(TranscriptTurn)
            .where(TranscriptTurn.negotiation_id == negotiation.id)
            .order_by(TranscriptTurn.id)
        ).all()
    )


def events(session, tx_id, event_type) -> list[AuditEvent]:
    query = select(AuditEvent).where(
        AuditEvent.transaction_id == tx_id, AuditEvent.event_type == event_type
    )
    return list(session.scalars(query).all())


# --- start() -------------------------------------------------------------------------------


async def test_start_speaks_and_writes_the_agent_transcript(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    llm = ScriptedLLMProvider([reply("Hi, I'm an AI assistant calling for a client.")])
    call = make_negotiation_call(session, tx, negotiation, llm)

    turn = await call.start()

    assert turn == AgentTurn("Hi, I'm an AI assistant calling for a client.")
    (transcript,) = transcripts(session, negotiation)
    assert (transcript.speaker, transcript.text) == ("agent", turn.say)


async def test_start_writes_input_received_and_response_sent_with_latency(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    llm = ScriptedLLMProvider([reply("Hi there.")])
    call = make_negotiation_call(session, tx, negotiation, llm)

    await call.start()

    (received,) = events(session, tx.id, "call.input_received")
    (sent,) = events(session, tx.id, "call.response_sent")
    assert sent.payload["say"] == "Hi there."
    assert sent.payload["end_call"] is False
    assert isinstance(sent.payload["latency_ms"], int) and sent.payload["latency_ms"] >= 0
    assert received.payload["text"]


# --- respond() -------------------------------------------------------------------------------


async def test_respond_writes_provider_transcript_before_agent_transcript(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    llm = ScriptedLLMProvider([reply("Thanks.")])
    call = make_negotiation_call(session, tx, negotiation, llm)

    await call.respond(CallerMessage("Yes, I'm free that day."))

    provider_turn, agent_turn = transcripts(session, negotiation)
    assert (provider_turn.speaker, provider_turn.text) == ("provider", "Yes, I'm free that day.")
    assert (agent_turn.speaker, agent_turn.text) == ("agent", "Thanks.")


async def test_respond_writes_the_transcript_before_the_engine_runs(session):
    """The acceptance-criteria case: a tool call inside this same turn must see what the
    provider just said, because amount_heard reads persisted transcript_turns, not the model's
    own arguments (contract 5: "no transcription race")."""
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    call_args = {
        "amount": 18000,
        "available": True,
        "coverage_hours": 6,
        "provider_refuses_negotiation": False,
        "provider_words": "18,000 for six hours",
    }
    llm = ScriptedLLMProvider(
        [tool_use(ToolCall("t1", "record_offer", call_args)), reply("Understood.")]
    )
    tools = NegotiationToolExecutor(session, tx, negotiation)
    call = make_negotiation_call(session, tx, negotiation, llm, tools=tools)

    await call.respond(CallerMessage("18,000 for six hours"))

    # If the transcript had been written after the engine ran, amount_heard would have seen
    # nothing yet and record_offer would have rejected the amount instead of storing it.
    offer = session.scalars(select(Offer).where(Offer.negotiation_id == negotiation.id)).one()
    assert offer.amount == 18000
    assert offer.decision == "MAY_ACCEPT"


# --- finish() --------------------------------------------------------------------------------


async def test_finish_sets_ended_at_reason_and_duration(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    llm = ScriptedLLMProvider([])
    finished_at = ANSWERED + timedelta(seconds=42)
    call = make_negotiation_call(session, tx, negotiation, llm, now=lambda: finished_at)

    await call.finish("caller hung up")

    assert negotiation.end_reason == "caller hung up"
    assert negotiation.ended_at == finished_at.isoformat(timespec="milliseconds")
    assert negotiation.duration_seconds == 42


async def test_finish_without_answered_at_leaves_duration_unset(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx, answered_at=None)
    llm = ScriptedLLMProvider([])
    call = make_negotiation_call(session, tx, negotiation, llm)

    await call.finish(None)

    assert negotiation.duration_seconds is None


# --- hard end --------------------------------------------------------------------------------


async def test_past_hard_end_skips_the_engine_and_speaks_from_db_state(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx, status="UNAVAILABLE")
    llm = ScriptedLLMProvider([])  # would raise if the engine were ever called
    past_deadline = ANSWERED + timedelta(seconds=181)
    call = make_negotiation_call(
        session, tx, negotiation, llm, hard_end_seconds=180, now=lambda: past_deadline
    )

    turn = await call.respond(CallerMessage("still talking"))

    assert turn == AgentTurn(
        "Thanks for letting me know. Goodbye.", end_call=True, note="time.hard_end"
    )
    assert events(session, tx.id, "time.hard_end")
    provider_turn, agent_turn = transcripts(session, negotiation)
    assert provider_turn.text == "still talking"
    assert agent_turn.text == turn.say


async def test_before_hard_end_runs_the_engine_normally(session):
    tx = make_tx(session)
    negotiation = make_negotiation(session, tx)
    llm = ScriptedLLMProvider([reply("Sure, tell me more.")])
    just_under = ANSWERED + timedelta(seconds=179)
    call = make_negotiation_call(
        session, tx, negotiation, llm, hard_end_seconds=180, now=lambda: just_under
    )

    turn = await call.respond(CallerMessage("hello"))

    assert turn == AgentTurn("Sure, tell me more.")
    assert events(session, tx.id, "time.hard_end") == []
