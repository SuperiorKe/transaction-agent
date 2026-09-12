"""NegotiationCall: a ConversationAgent that owns one call's persistence (epic #10, contract 5).

`CallCoordinator` (`app/calls.py`) drives any `ConversationAgent` — it doesn't know this one talks
to a database. `NegotiationCall` wraps an already-built `NegotiationEngine` (LLM + tools + prompt
for this specific transaction/negotiation) and adds exactly what the engine itself must not know
about: writing transcript turns, timing model latency into the audit trail, and the hard-end circuit
breaker in `TODOS.md` ("nothing currently reads or enforces `CALL_HARD_END_SECONDS`") — solved here,
in the one place that has both a clock and the DB state to speak a closing line without the model.
"""

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agent.engine import NegotiationEngine
from app.agent.tools import closing_say
from app.audit import record_event
from app.conversation import AgentTurn, CallerMessage
from app.models import Negotiation, Transaction, TranscriptTurn

# Transcript/audit label only, never spoken — engine.py owns the real CALL_CONNECTED marker text.
CALL_CONNECTED_LABEL = "[call connected]"


class NegotiationCall:
    """One call's `ConversationAgent`. Construct a fresh instance per call, per negotiation."""

    def __init__(
        self,
        session: Session,
        tx: Transaction,
        negotiation: Negotiation,
        engine: NegotiationEngine,
        *,
        hard_end_seconds: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._tx = tx
        self._negotiation = negotiation
        self._engine = engine
        self._hard_end_seconds = hard_end_seconds
        self._now = now

    async def start(self) -> AgentTurn:
        turn = await self._run(self._engine.start(), input_text=CALL_CONNECTED_LABEL)
        self._session.commit()
        return turn

    async def respond(self, message: CallerMessage) -> AgentTurn:
        # Written before the engine runs, so a record_offer tool call in this same turn sees it
        # via the last-3-provider-turns amount_heard check (contract 5) — no transcription race.
        self._write_transcript("provider", message.text)
        if self._past_hard_end():
            turn = self._hard_end()
        else:
            turn = await self._run(self._engine.respond(message), input_text=message.text)
        self._session.commit()
        return turn

    async def finish(self, reason: str | None) -> None:
        ended = self._now()
        self._negotiation.ended_at = ended.isoformat(timespec="milliseconds")
        self._negotiation.end_reason = reason
        if self._negotiation.answered_at is not None:
            answered = datetime.fromisoformat(self._negotiation.answered_at)
            self._negotiation.duration_seconds = round((ended - answered).total_seconds())
        await self._engine.finish(reason)
        self._session.commit()

    def _past_hard_end(self) -> bool:
        if self._negotiation.answered_at is None:
            return False
        answered = datetime.fromisoformat(self._negotiation.answered_at)
        return (self._now() - answered).total_seconds() >= self._hard_end_seconds

    def _hard_end(self) -> AgentTurn:
        say = closing_say(self._session, self._tx, self._negotiation)
        payload = {"status": self._negotiation.status}
        record_event(self._session, self._tx.id, "time.hard_end", payload)
        self._write_transcript("agent", say)
        return AgentTurn(say, end_call=True, note="time.hard_end")

    async def _run(self, turn_awaitable: Awaitable[AgentTurn], *, input_text: str) -> AgentTurn:
        record_event(self._session, self._tx.id, "call.input_received", {"text": input_text})
        started = time.monotonic()
        turn = await turn_awaitable
        latency_ms = round((time.monotonic() - started) * 1000)
        payload = {
            "say": turn.say,
            "end_call": turn.end_call,
            "note": turn.note,
            "latency_ms": latency_ms,
        }
        record_event(self._session, self._tx.id, "call.response_sent", payload)
        self._write_transcript("agent", turn.say)
        return turn

    def _write_transcript(self, speaker: str, text: str) -> None:
        self._session.add(
            TranscriptTurn(negotiation_id=self._negotiation.id, speaker=speaker, text=text)
        )
        self._session.flush()
