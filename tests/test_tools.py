"""Offline tests for NegotiationToolExecutor (epic #10, contract 5). No LLM, no telephony."""

import json

from sqlalchemy import select

from app.agent.engine import ToolOutcome
from app.agent.tools import (
    CONFIRMATION_TOOLS,
    NEGOTIATION_TOOLS,
    REQUIRED_TERMS,
    NegotiationToolExecutor,
)
from app.llm.base import ToolCall
from app.models import (
    Approval,
    AuditEvent,
    Negotiation,
    Offer,
    Provider,
    Transaction,
    TranscriptTurn,
)

CAP = 20000


def make_tx(session, **overrides) -> Transaction:
    values = dict(
        request="Photographer for Monday in Nairobi. Maximum KES 20,000.",
        service_date="2026-09-14",  # a Monday, per the epic's own canonical scenario
        location="Nairobi",
        max_budget=CAP,
        max_attempts=2,
    )
    values.update(overrides)
    tx = Transaction(**values)
    session.add(tx)
    session.flush()
    return tx


def make_provider(session, **overrides) -> Provider:
    values = dict(name="Test Photographer", phone="+254100000099", location="Nairobi", priority=1)
    values.update(overrides)
    provider = Provider(**values)
    session.add(provider)
    session.flush()
    return provider


def make_negotiation(session, tx, provider, **overrides) -> Negotiation:
    values = dict(transaction_id=tx.id, provider_id=provider.id, kind="negotiation")
    values.update(overrides)
    negotiation = Negotiation(**values)
    session.add(negotiation)
    session.flush()
    return negotiation


def make_call(session, **tx_overrides):
    tx = make_tx(session, **tx_overrides)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider)
    return tx, provider, negotiation, NegotiationToolExecutor(session, tx, negotiation)


def add_offer(session, negotiation, *, decision, amount=None, coverage_hours=None, **kw) -> Offer:
    values = dict(
        negotiation_id=negotiation.id,
        amount=amount,
        available=True,
        coverage_hours=coverage_hours,
        provider_words="offer",
        source="provider_quote",
        decision=decision,
    )
    values.update(kw)
    offer = Offer(**values)
    session.add(offer)
    session.flush()
    return offer


def speak(session, negotiation, text: str) -> None:
    session.add(TranscriptTurn(negotiation_id=negotiation.id, speaker="provider", text=text))
    session.flush()


def offers(session, negotiation) -> list[Offer]:
    return list(session.scalars(select(Offer).where(Offer.negotiation_id == negotiation.id)).all())


def events(session, tx_id, event_type=None) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.transaction_id == tx_id)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    return list(session.scalars(query).all())


async def call_tool(executor, name, **args) -> tuple[ToolOutcome, dict]:
    outcome = await executor.execute(ToolCall("t1", name, args))
    return outcome, json.loads(outcome.content)


def offer_args(**overrides) -> dict:
    values = dict(available=True, provider_refuses_negotiation=False, provider_words="offer")
    values.update(overrides)
    return values


# --- specs() picks the right tool set ---------------------------------------------------------


def test_specs_for_negotiation_kind(session):
    _, _, _, executor = make_call(session)
    assert {spec.name for spec in executor.specs()} == set(NEGOTIATION_TOOLS)


def test_specs_for_confirmation_kind(session):
    tx, provider, _, _ = make_call(session)
    confirmation = make_negotiation(session, tx, provider, kind="confirmation")
    executor = NegotiationToolExecutor(session, tx, confirmation)
    assert {spec.name for spec in executor.specs()} == set(CONFIRMATION_TOOLS)


# --- get_transaction_policy ---------------------------------------------------------------------


async def test_get_transaction_policy_reports_budget_date_and_progress(session):
    tx, _, negotiation, executor = make_call(session, max_attempts=2)
    negotiation.attempt_count = 1

    _, payload = await call_tool(executor, "get_transaction_policy")

    assert payload["service_date_spoken"] == "Monday, 14 September"
    assert payload["location"] == "Nairobi"
    assert payload["max_budget"] == CAP
    assert payload["counteroffers_made"] == 1
    assert payload["counteroffers_allowed"] == 2
    assert payload["required_terms"] == list(REQUIRED_TERMS)


# --- record_offer: the six evaluate_offer outcomes -----------------------------------------------


async def test_record_offer_within_budget_may_accept(session):
    tx, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "18,000 for six hours")

    outcome, payload = await call_tool(
        executor, "record_offer", **offer_args(amount=18000, coverage_hours=6)
    )

    (offer,) = offers(session, negotiation)
    assert payload == {"ok": True, "decision": "MAY_ACCEPT", "offer_id": offer.id}
    assert outcome.is_error is False
    assert offer.decision == "MAY_ACCEPT"
    assert events(session, tx.id, "offer.recorded")


async def test_record_offer_over_budget_counters(session):
    tx, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "23,000 for six hours")

    outcome, payload = await call_tool(
        executor, "record_offer", **offer_args(amount=23000, coverage_hours=6)
    )

    assert payload["decision"] == "COUNTER"
    assert payload["next_counteroffer"] == 19500
    assert "say" not in payload
    assert outcome.end_call is False


async def test_record_offer_not_available_marks_unavailable(session):
    tx, _, negotiation, executor = make_call(session)

    outcome, payload = await call_tool(executor, "record_offer", **offer_args(available=False))

    assert payload == {
        "ok": True,
        "decision": "UNAVAILABLE",
        "say": "Thanks for letting me know. Goodbye.",
    }
    # record_offer never hangs up by itself — only the model's own end_call call does (contract 5).
    assert outcome.end_call is False
    assert negotiation.status == "UNAVAILABLE"


async def test_record_offer_deposit_required_escalates(session):
    tx, _, negotiation, executor = make_call(session)

    outcome, payload = await call_tool(
        executor, "record_offer", **offer_args(deposit_required=True)
    )

    assert payload["decision"] == "MUST_ESCALATE"
    assert outcome.end_call is False
    assert negotiation.escalation_trigger == "deposit_or_payment_request"
    assert negotiation.status == "ESCALATED"
    assert events(session, tx.id, "escalation.raised")


async def test_record_offer_missing_price_clarifies_then_escalates(session):
    tx, _, negotiation, executor = make_call(session)

    _, first = await call_tool(executor, "record_offer", **offer_args())
    assert first["decision"] == "CLARIFY"
    assert first["clarify_term"] == "price"
    assert negotiation.clarifications == 1

    outcome, second = await call_tool(executor, "record_offer", **offer_args())
    assert second["decision"] == "MUST_ESCALATE"
    assert negotiation.escalation_trigger == "incomplete_terms"
    assert outcome.end_call is False


async def test_record_offer_missing_coverage_hours_clarifies(session):
    _, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "18,000 for that day")

    _, payload = await call_tool(executor, "record_offer", **offer_args(amount=18000))

    assert payload["decision"] == "CLARIFY"
    assert payload["clarify_term"] == "coverage_hours"


async def test_record_offer_provider_refuses_negotiation_stops(session):
    _, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "23,000, I don't negotiate")

    outcome, payload = await call_tool(
        executor, "record_offer", **offer_args(amount=23000, provider_refuses_negotiation=True)
    )

    assert payload["decision"] == "STOP_NEGOTIATING"
    assert outcome.end_call is False
    assert negotiation.status == "OUTSIDE_AUTHORITY"


async def test_record_offer_after_attempts_exhausted_stops(session):
    _, _, negotiation, executor = make_call(session)
    negotiation.attempt_count = 2  # both allowed counters already spoken
    speak(session, negotiation, "21,000 final")

    outcome, payload = await call_tool(executor, "record_offer", **offer_args(amount=21000))

    assert payload["decision"] == "STOP_NEGOTIATING"
    assert "21,000" in payload["say"]
    assert outcome.end_call is False


async def test_record_offer_unheard_amount_is_rejected_and_not_stored(session):
    _, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "It's been twenty three years in the business")

    outcome, payload = await call_tool(executor, "record_offer", **offer_args(amount=23000))

    assert outcome.is_error is True
    assert payload == {"ok": False, "say": "Sorry, could you repeat the price?"}
    assert offers(session, negotiation) == []
    assert negotiation.price_rejections == 1
    assert events(session, negotiation.transaction_id, "tool.rejected")


async def test_record_offer_unheard_amount_twice_escalates(session):
    tx, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "mumbles something unclear")

    await call_tool(executor, "record_offer", **offer_args(amount=23000))
    outcome, payload = await call_tool(executor, "record_offer", **offer_args(amount=23000))

    assert outcome.is_error is True
    assert outcome.end_call is False
    escalate_say = "I can't agree to that on this call. I'll pass it to my client. Goodbye."
    assert payload["say"] == escalate_say
    assert negotiation.escalation_trigger == "unclear_audio"
    assert negotiation.status == "ESCALATED"
    assert offers(session, negotiation) == []


async def test_record_offer_resets_a_stale_agreed_status(session):
    """S8: the provider corrects their own in-cap price before the call ends. Because record_offer
    and record_agreement never hang up by themselves, a second record_offer can still arrive after
    an earlier one was already accepted — and must not leave a stale AGREED status behind."""
    _, _, negotiation, executor = make_call(session)
    speak(session, negotiation, "20,000 for six hours")
    _, first = await call_tool(
        executor, "record_offer", **offer_args(amount=20000, coverage_hours=6)
    )
    assert first["decision"] == "MAY_ACCEPT"
    await call_tool(executor, "record_agreement", offer_id=first["offer_id"])
    assert negotiation.status == "AGREED"

    speak(session, negotiation, "22,000 for six hours")
    _, second = await call_tool(
        executor, "record_offer", **offer_args(amount=22000, coverage_hours=6)
    )

    assert second["decision"] == "COUNTER"
    assert negotiation.status == "IN_PROGRESS"


# --- make_counteroffer ----------------------------------------------------------------------


async def test_make_counteroffer_before_a_counter_decision_is_rejected(session):
    _, _, negotiation, executor = make_call(session)

    outcome, _ = await call_tool(executor, "make_counteroffer", amount=19500)

    assert outcome.is_error is True
    assert negotiation.attempt_count == 0


async def test_make_counteroffer_wrong_amount_is_rejected(session):
    _, _, negotiation, executor = make_call(session)
    add_offer(session, negotiation, decision="COUNTER", amount=23000)

    outcome, _ = await call_tool(executor, "make_counteroffer", amount=19000)

    assert outcome.is_error is True
    assert negotiation.attempt_count == 0


async def test_make_counteroffer_accepts_first_counter(session):
    tx, _, negotiation, executor = make_call(session)
    add_offer(session, negotiation, decision="COUNTER", amount=23000)

    outcome, payload = await call_tool(executor, "make_counteroffer", amount=19500)

    assert outcome.is_error is False
    assert "19,500" in payload["say"]
    assert negotiation.attempt_count == 1
    assert events(session, tx.id, "counteroffer.made")


async def test_make_counteroffer_final_counter_reveals_the_cap(session):
    _, _, negotiation, executor = make_call(session)
    negotiation.attempt_count = 1
    add_offer(session, negotiation, decision="COUNTER", amount=21000)

    outcome, payload = await call_tool(executor, "make_counteroffer", amount=20000)

    assert negotiation.attempt_count == 2
    assert "not authorized to agree above KES 20,000" in payload["say"]


async def test_make_counteroffer_third_attempt_after_max_is_rejected(session):
    _, _, negotiation, executor = make_call(session)
    negotiation.attempt_count = 2  # already used both allowed counters
    add_offer(session, negotiation, decision="COUNTER", amount=21000)

    outcome, _ = await call_tool(executor, "make_counteroffer", amount=20000)

    assert outcome.is_error is True
    assert negotiation.attempt_count == 2


# --- record_agreement -------------------------------------------------------------------------


async def test_record_agreement_accepts_latest_may_accept_offer(session):
    _, _, negotiation, executor = make_call(session)
    offer = add_offer(session, negotiation, decision="MAY_ACCEPT", amount=18000, coverage_hours=6)

    outcome, payload = await call_tool(executor, "record_agreement", offer_id=offer.id)

    assert outcome.is_error is False
    assert outcome.end_call is False  # the model must still call end_call itself to hang up
    assert "18,000" in payload["say"]
    assert negotiation.status == "AGREED"


async def test_record_agreement_rejects_offer_above_the_cap(session):
    _, _, negotiation, executor = make_call(session)
    offer = add_offer(session, negotiation, decision="COUNTER", amount=23000)

    outcome, _ = await call_tool(executor, "record_agreement", offer_id=offer.id)

    assert outcome.is_error is True
    assert negotiation.status != "AGREED"


async def test_record_agreement_rejects_a_stale_offer(session):
    _, _, negotiation, executor = make_call(session)
    stale = add_offer(session, negotiation, decision="MAY_ACCEPT", amount=18000, coverage_hours=6)
    add_offer(session, negotiation, decision="COUNTER", amount=23000)  # provider changed their mind

    outcome, _ = await call_tool(executor, "record_agreement", offer_id=stale.id)

    assert outcome.is_error is True
    assert negotiation.status != "AGREED"


# --- escalate --------------------------------------------------------------------------------


async def test_escalate_sets_trigger_and_returns_say(session):
    tx, _, negotiation, executor = make_call(session)

    outcome, payload = await call_tool(
        executor,
        "escalate",
        trigger="sensitive_information_request",
        provider_words="asked for home address",
    )

    assert outcome.end_call is False
    assert negotiation.escalation_trigger == "sensitive_information_request"
    assert negotiation.status == "ESCALATED"
    assert events(session, tx.id, "escalation.raised")


async def test_escalate_rejects_unknown_trigger(session):
    _, _, negotiation, executor = make_call(session)

    outcome, _ = await call_tool(executor, "escalate", trigger="bogus", provider_words="x")

    assert outcome.is_error is True
    assert negotiation.escalation_trigger is None


# --- end_call: DB state decides the say, not the model's reason -------------------------------


async def test_end_call_ignores_reason_and_uses_unavailable_status(session):
    _, _, negotiation, executor = make_call(session)
    negotiation.status = "UNAVAILABLE"

    outcome, payload = await call_tool(executor, "end_call", reason="this text must be ignored")

    assert payload["say"] == "Thanks for letting me know. Goodbye."
    assert outcome.end_call is True


async def test_end_call_after_agreement_speaks_the_may_accept_line(session):
    _, _, negotiation, executor = make_call(session)
    add_offer(session, negotiation, decision="MAY_ACCEPT", amount=18000, coverage_hours=6)
    negotiation.status = "AGREED"

    outcome, payload = await call_tool(executor, "end_call", reason="deal done")

    assert "18,000" in payload["say"]
    assert outcome.end_call is True


async def test_end_call_falls_back_to_a_plain_goodbye(session):
    _, _, _, executor = make_call(session)

    outcome, payload = await call_tool(executor, "end_call", reason="anything")

    assert payload["say"] == "Thanks for your time. Goodbye."
    assert outcome.end_call is True


# --- confirmation call -------------------------------------------------------------------------


def confirmation_executor(session, tx, provider):
    negotiation = make_negotiation(session, tx, provider, kind="confirmation")
    return negotiation, NegotiationToolExecutor(session, tx, negotiation)


async def test_get_approved_terms_reads_the_approved_offer(session):
    tx, provider, original, _ = make_call(session)
    offer = add_offer(session, original, decision="MAY_ACCEPT", amount=18000, coverage_hours=6)
    session.add(Approval(transaction_id=tx.id, offer_id=offer.id, decision="APPROVED"))
    session.flush()
    _, executor = confirmation_executor(session, tx, provider)

    _, payload = await call_tool(executor, "get_approved_terms")

    assert payload["provider_name"] == provider.name
    assert payload["approved_amount"] == 18000
    assert payload["coverage_hours"] == 6
    assert payload["service_date_spoken"] == "Monday, 14 September"


async def test_record_confirmation_confirmed_marks_negotiation_confirmed(session):
    tx, provider, _, _ = make_call(session)
    negotiation, executor = confirmation_executor(session, tx, provider)

    outcome, _ = await call_tool(
        executor, "record_confirmation", confirmed=True, provider_words="Yes, confirmed"
    )

    assert outcome.end_call is False
    assert negotiation.status == "CONFIRMED"


async def test_record_confirmation_no_longer_available(session):
    tx, provider, _, _ = make_call(session)
    negotiation, executor = confirmation_executor(session, tx, provider)

    await call_tool(
        executor,
        "record_confirmation",
        confirmed=False,
        provider_words="Sorry, no longer available",
        no_longer_available=True,
    )

    assert negotiation.status == "DECLINED_BY_PROVIDER"
    (offer,) = offers(session, negotiation)
    assert offer.available is False
    assert offer.source == "confirmation_call"


async def test_record_confirmation_changed_amount_flags_terms_changed(session):
    tx, provider, original, _ = make_call(session)
    approved = add_offer(session, original, decision="MAY_ACCEPT", amount=18000, coverage_hours=6)
    session.add(Approval(transaction_id=tx.id, offer_id=approved.id, decision="APPROVED"))
    session.flush()
    negotiation, executor = confirmation_executor(session, tx, provider)

    await call_tool(
        executor,
        "record_confirmation",
        confirmed=False,
        provider_words="Actually it's 22,000 now",
        changed_amount=22000,
    )

    assert negotiation.status == "TERMS_CHANGED"
    (new_offer,) = offers(session, negotiation)
    assert new_offer.amount == 22000
    assert new_offer.coverage_hours == 6  # inherited from the approved offer
    assert new_offer.source == "confirmation_call"
    assert new_offer.decision == "MUST_ESCALATE"  # 22,000 is above the 20,000 cap


async def test_record_confirmation_rejects_unconfirmed_with_no_change(session):
    tx, provider, _, _ = make_call(session)
    _, executor = confirmation_executor(session, tx, provider)

    outcome, _ = await call_tool(
        executor, "record_confirmation", confirmed=False, provider_words="no"
    )

    assert outcome.is_error is True
