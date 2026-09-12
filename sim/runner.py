"""Drives one scenario through CallCoordinator + FakeTelephonyProvider + the real Anthropic API.

No telephony, no speech-to-text: `FakeTelephonyProvider` stands in for Africa's Talking, and each
persona line is fed as plain text, exactly like an already-transcribed caller turn. Only
`ANTHROPIC_API_KEY` is required (epic #10, issue #4) — this is the one place in the codebase
allowed to import `app.llm.anthropic` outside of `app/llm/` itself, since `sim/` sits outside the
`app/agent` vendor-isolation boundary enforced by `tests/test_architecture.py`.

"Temp SQLite DB" (issue #4's wording) is an in-memory database here, not a temp file — this repo's
own test suite (`tests/conftest.py`) already treats in-memory + `StaticPool` as the standard
throwaway-DB pattern, so scenarios follow the same convention rather than managing temp files.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.engine import NegotiationEngine
from app.agent.prompts import CONFIRMATION_SYSTEM_PROMPT, NEGOTIATION_SYSTEM_PROMPT
from app.agent.session import NegotiationCall
from app.agent.tools import NegotiationToolExecutor
from app.calls import CallCoordinator
from app.config import get_settings
from app.db import make_engine
from app.llm.anthropic import AnthropicLLMProvider
from app.llm.base import LLMProvider
from app.models import (
    Approval,
    AuditEvent,
    Base,
    Negotiation,
    Offer,
    Provider,
    Transaction,
    TranscriptTurn,
)
from app.policy import OfferTerms
from app.telephony.fake import FakeTelephonyProvider, answered, ended, said, silence
from sim.personas import PERSONAS, Persona

MAX_TURNS = 12
CAP = 20000
MAX_ATTEMPTS = 2
SERVICE_DATE = "2026-09-14"
LOCATION = "Nairobi"

# S12/S13 are confirmation calls, so they need a prior, already-approved negotiation to confirm.
# Contract 5 doesn't specify these terms (only the confirmation call's own script) — chosen to be
# unambiguously within budget so get_approved_terms/record_confirmation have something real to read.
APPROVED_AMOUNT = 19000
APPROVED_COVERAGE_HOURS = 6.0


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    kind: str
    transcript: list[tuple[str, str]]
    tool_calls: list[dict[str, Any]]
    latencies_ms: list[int]
    counters_made: list[int]
    negotiation_status: str
    escalation_trigger: str | None
    counteroffers_made: int
    offer_amounts: list[int | None]
    final_offer: OfferTerms | None
    cap: int
    provider_name: str
    service_date: str
    ended_via: str  # "end_call" | "turn_limit"


def _build_session() -> Session:
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _setup(session: Session, persona: Persona) -> tuple[Transaction, Provider, Negotiation]:
    tx = Transaction(
        request=(
            "Photographer for Monday in Nairobi. Maximum KES 20,000. "
            "Negotiate twice; never agree above KES 20,000 without my approval."
        ),
        service_date=SERVICE_DATE,
        location=LOCATION,
        max_budget=CAP,
        max_attempts=MAX_ATTEMPTS,
    )
    session.add(tx)
    session.flush()

    number = int(persona.id[1:])
    provider = Provider(
        name=f"Provider {persona.id}",
        phone=f"+254100000{number:03d}",
        location=LOCATION,
        priority=1,
    )
    session.add(provider)
    session.flush()

    answered_at = datetime.now(UTC).isoformat(timespec="milliseconds")
    if persona.kind == "confirmation":
        prior = Negotiation(
            transaction_id=tx.id,
            provider_id=provider.id,
            kind="negotiation",
            status="AGREED",
            answered_at=answered_at,
        )
        session.add(prior)
        session.flush()
        approved_offer = Offer(
            negotiation_id=prior.id,
            amount=APPROVED_AMOUNT,
            available=True,
            coverage_hours=APPROVED_COVERAGE_HOURS,
            provider_words=f"{APPROVED_AMOUNT} for {APPROVED_COVERAGE_HOURS:g} hours",
            source="provider_quote",
            decision="MAY_ACCEPT",
        )
        session.add(approved_offer)
        session.flush()
        session.add(Approval(transaction_id=tx.id, offer_id=approved_offer.id, decision="APPROVED"))
        session.flush()

    negotiation = Negotiation(
        transaction_id=tx.id,
        provider_id=provider.id,
        kind=persona.kind,
        answered_at=answered_at,
    )
    session.add(negotiation)
    session.flush()
    return tx, provider, negotiation


async def run_scenario(scenario_id: str, *, llm: LLMProvider | None = None) -> ScenarioResult:
    persona = PERSONAS[scenario_id]
    settings = get_settings()
    session = _build_session()
    tx, provider, negotiation = _setup(session, persona)

    provider_llm = llm or AnthropicLLMProvider(
        model=settings.anthropic_model, api_key=settings.anthropic_api_key
    )
    prompt = (
        NEGOTIATION_SYSTEM_PROMPT if persona.kind == "negotiation" else CONFIRMATION_SYSTEM_PROMPT
    )
    tools = NegotiationToolExecutor(session, tx, negotiation)
    engine = NegotiationEngine(provider_llm, system_prompt=prompt, tools=tools)
    call = NegotiationCall(
        session, tx, negotiation, engine, hard_end_seconds=settings.call_hard_end_seconds
    )

    session_id = scenario_id
    coordinator = CallCoordinator(FakeTelephonyProvider(), lambda _sid: call)
    lines = iter(persona.lines)

    turn_count = 1
    reply = await coordinator.handle_callback(answered(session_id))
    ended_via = "turn_limit"
    while True:
        turn = json.loads(reply.body)
        if turn["end_call"]:
            ended_via = "end_call"
            break
        if turn_count >= MAX_TURNS:
            break
        line = next(lines, None)
        form = said(session_id, line) if line is not None else silence(session_id)
        reply = await coordinator.handle_callback(form)
        turn_count += 1

    await coordinator.handle_callback(ended(session_id, reason=ended_via))

    return _collect_result(session, persona, tx, negotiation, provider, ended_via)


def _collect_result(
    session: Session,
    persona: Persona,
    tx: Transaction,
    negotiation: Negotiation,
    provider: Provider,
    ended_via: str,
) -> ScenarioResult:
    transcript = [
        (turn.speaker, turn.text)
        for turn in session.scalars(
            select(TranscriptTurn)
            .where(TranscriptTurn.negotiation_id == negotiation.id)
            .order_by(TranscriptTurn.id)
        )
    ]

    def audit(event_type: str) -> list[AuditEvent]:
        query = select(AuditEvent).where(
            AuditEvent.transaction_id == tx.id, AuditEvent.event_type == event_type
        )
        return list(session.scalars(query.order_by(AuditEvent.id)))

    tool_calls = [event.payload for event in audit("tool.called")]
    latencies_ms = [event.payload["latency_ms"] for event in audit("call.response_sent")]
    counters_made = [event.payload["amount"] for event in audit("counteroffer.made")]

    offers = list(
        session.scalars(
            select(Offer).where(Offer.negotiation_id == negotiation.id).order_by(Offer.id)
        )
    )
    offer_amounts = [offer.amount for offer in offers]
    latest = offers[-1] if offers else None
    final_offer = (
        OfferTerms(
            available=latest.available,
            amount=latest.amount,
            coverage_hours=latest.coverage_hours,
            deposit_required=latest.deposit_required,
            provider_refuses_negotiation=latest.provider_refuses_negotiation,
        )
        if latest is not None
        else None
    )

    result = ScenarioResult(
        scenario_id=persona.id,
        kind=persona.kind,
        transcript=transcript,
        tool_calls=tool_calls,
        latencies_ms=latencies_ms,
        counters_made=counters_made,
        negotiation_status=negotiation.status,
        escalation_trigger=negotiation.escalation_trigger,
        counteroffers_made=negotiation.attempt_count,
        offer_amounts=offer_amounts,
        final_offer=final_offer,
        cap=tx.max_budget,
        provider_name=provider.name,
        service_date=tx.service_date,
        ended_via=ended_via,
    )
    session.close()
    return result
