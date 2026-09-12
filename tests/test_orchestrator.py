"""Offline tests for app/orchestrator.py (epic #10, contracts 2 and 4).

FakeTelephonyProvider + a fake clock; no vendor SDK, no network. Organized to walk every row of
the state table in the issue, then the callback -> orchestrator mapping.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.conversation import AgentTurn
from app.models import (
    Approval,
    AuditEvent,
    Negotiation,
    Offer,
    Provider,
    Recommendation,
    Transaction,
)
from app.orchestrator import (
    ApprovalRequired,
    CallGuardBlocked,
    OrchestratedCall,
    StaleOffer,
    UnrecognizedCallAgent,
    advance_from_unavailable,
    approve,
    decline,
    on_call_answered,
    on_call_ended,
    place_call,
    resolve_negotiation,
    retry_confirmation,
    schedule_confirmation_retry,
    select_provider,
    start_confirmation,
)
from app.states import NegStatus, TxStatus, transition
from app.telephony.base import TelephonyError
from app.telephony.fake import FakeTelephonyProvider

CAP = 20000


# --- fixtures / helpers -----------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


class FailingTelephony(FakeTelephonyProvider):
    """Fails `place_call` `fail_times` times, then behaves like the real fake."""

    def __init__(self, fail_times: int = 0) -> None:
        super().__init__()
        self._fail_times = fail_times

    async def place_call(self, to_number: str):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise TelephonyError("simulated telephony failure")
        return await super().place_call(to_number)


def make_tx(session, *, status: str = TxStatus.CREATED.value, **overrides) -> Transaction:
    values = dict(
        request="Photographer for Monday in Nairobi. Maximum KES 20,000.",
        service_date="2026-09-14",
        location="Nairobi",
        max_budget=CAP,
        max_attempts=2,
        status=status,
    )
    values.update(overrides)
    tx = Transaction(**values)
    session.add(tx)
    session.flush()
    return tx


def make_provider(session, **overrides) -> Provider:
    values = dict(
        name="Studio A", phone="+254711000001", location="Nairobi", priority=1, active=True
    )
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


def make_offer(session, negotiation, **overrides) -> Offer:
    values = dict(
        negotiation_id=negotiation.id,
        amount=None,
        available=True,
        coverage_hours=None,
        provider_words="offer",
        source="provider_quote",
        decision="MAY_ACCEPT",
    )
    values.update(overrides)
    offer = Offer(**values)
    session.add(offer)
    session.flush()
    return offer


NOW = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))


# --- select_provider: CREATED/NEXT_PROVIDER -> PROVIDER_SELECTED/FAILED ------------------------


async def test_created_to_provider_selected_picks_lowest_priority_active_untried(session):
    tx = make_tx(session)
    make_provider(session, name="Low priority", phone="+254711000002", priority=2)
    first = make_provider(session, name="High priority", phone="+254711000001", priority=1)

    chosen = select_provider(session, tx)

    assert chosen.id == first.id
    assert tx.status == TxStatus.PROVIDER_SELECTED.value
    assert tx.current_provider_id == first.id


async def test_created_to_failed_when_no_active_providers(session):
    tx = make_tx(session)
    make_provider(session, active=False)

    chosen = select_provider(session, tx)

    assert chosen is None
    assert tx.status == TxStatus.FAILED.value


async def test_select_provider_skips_already_tried_providers(session):
    tx = make_tx(session, status=TxStatus.NEXT_PROVIDER.value)
    tried = make_provider(session, priority=1)
    untried = make_provider(session, name="Studio B", phone="+254711000002", priority=2)
    make_negotiation(session, tx, tried)  # already tried

    chosen = select_provider(session, tx)

    assert chosen.id == untried.id
    assert tx.status == TxStatus.PROVIDER_SELECTED.value


# --- place_call: PROVIDER_SELECTED -> CALLING / UNAVAILABLE ------------------------------------


async def test_provider_selected_to_calling_on_successful_dial(session):
    tx = make_tx(session, status=TxStatus.PROVIDER_SELECTED.value)
    provider = make_provider(session)
    telephony = FakeTelephonyProvider()

    negotiation = await place_call(
        session, tx, provider, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.CALLING.value
    assert negotiation.provider_call_id == "fake-1"
    assert telephony.placed_calls == [provider.phone]


async def test_provider_selected_to_unavailable_after_two_telephony_errors(session):
    tx = make_tx(session, status=TxStatus.PROVIDER_SELECTED.value)
    provider = make_provider(session)
    telephony = FailingTelephony(fail_times=2)

    negotiation = await place_call(
        session, tx, provider, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.UNAVAILABLE.value
    assert negotiation.status == NegStatus.FAILED.value


async def test_place_call_recovers_after_a_single_telephony_error(session):
    tx = make_tx(session, status=TxStatus.PROVIDER_SELECTED.value)
    provider = make_provider(session)
    telephony = FailingTelephony(fail_times=1)

    await place_call(session, tx, provider, telephony=telephony, max_calls_per_day=40, now=NOW)

    assert tx.status == TxStatus.CALLING.value


async def test_place_call_raises_call_guard_blocked_and_writes_audit(session):
    tx = make_tx(session, status=TxStatus.PROVIDER_SELECTED.value)
    provider = make_provider(session)
    other_tx = make_tx(session)
    for _ in range(2):
        make_negotiation(
            session, other_tx, provider, created_at=NOW().isoformat(timespec="milliseconds")
        )
    telephony = FakeTelephonyProvider()

    with pytest.raises(CallGuardBlocked):
        await place_call(session, tx, provider, telephony=telephony, max_calls_per_day=2, now=NOW)

    assert tx.status == TxStatus.PROVIDER_SELECTED.value  # unchanged: guard fires before dialing
    assert telephony.placed_calls == []
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "call.guard_blocked")
    ).all()
    assert len(events) == 1


async def test_call_guard_only_counts_todays_nairobi_calls(session):
    tx = make_tx(session, status=TxStatus.PROVIDER_SELECTED.value)
    provider = make_provider(session)
    other_tx = make_tx(session)
    yesterday = NOW() - timedelta(days=1)
    make_negotiation(
        session, other_tx, provider, created_at=yesterday.isoformat(timespec="milliseconds")
    )
    telephony = FakeTelephonyProvider()

    await place_call(session, tx, provider, telephony=telephony, max_calls_per_day=1, now=NOW)

    assert tx.status == TxStatus.CALLING.value  # yesterday's call doesn't count against today


# --- on_call_answered: CALLING -> NEGOTIATING --------------------------------------------------


async def test_calling_to_negotiating_on_first_call_answered(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider, provider_call_id="CA1")

    on_call_answered(session, tx, negotiation, now=NOW)

    assert tx.status == TxStatus.NEGOTIATING.value
    assert negotiation.status == NegStatus.IN_PROGRESS.value
    assert negotiation.answered_at is not None


async def test_repeat_call_answered_is_a_no_op(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider, provider_call_id="CA1")
    on_call_answered(session, tx, negotiation, now=NOW)
    first_answered_at = negotiation.answered_at

    on_call_answered(session, tx, negotiation, now=NOW)  # a second "answered" for the same call

    assert negotiation.answered_at == first_answered_at
    assert tx.status == TxStatus.NEGOTIATING.value


# --- on_call_ended, never answered: CALLING -> UNAVAILABLE -------------------------------------


async def test_calling_to_unavailable_when_call_ends_before_it_was_answered(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    provider = make_provider(session, active=False)  # no fallback provider, isolates this row
    negotiation = make_negotiation(session, tx, provider, end_reason="no-answer")
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert negotiation.status == NegStatus.NO_ANSWER.value
    assert tx.status == TxStatus.FAILED.value  # cascaded: UNAVAILABLE -> FAILED, no providers left
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "DECLINE"


async def test_unavailable_cascades_to_next_provider_when_one_is_untried(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    first = make_provider(session, priority=1)
    second = make_provider(session, name="Studio B", phone="+254711000002", priority=2)
    negotiation = make_negotiation(session, tx, first, end_reason="failed")
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert negotiation.status == NegStatus.FAILED.value
    assert tx.status == TxStatus.CALLING.value  # dialed the second provider automatically
    assert tx.current_provider_id == second.id
    assert telephony.placed_calls == [second.phone]


# --- on_call_ended, answered: AGREED / OUTSIDE_AUTHORITY / ESCALATED / UNAVAILABLE -------------


async def test_negotiating_to_agreed_within_policy_writes_accept_recommendation(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        status=NegStatus.AGREED.value,
        answered_at=NOW().isoformat(),
        end_call_requested=True,
    )
    offer = make_offer(session, negotiation, amount=19000, coverage_hours=4, decision="MAY_ACCEPT")
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.RESULT_READY.value
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "ACCEPT"
    assert rec.offer_id == offer.id
    assert "dropped" not in rec.reason


async def test_negotiating_to_outside_authority_on_outside_authority_status(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        status=NegStatus.OUTSIDE_AUTHORITY.value,
        answered_at=NOW().isoformat(),
        end_call_requested=True,
    )
    make_offer(session, negotiation, amount=23000, decision="STOP_NEGOTIATING")
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.AWAITING_APPROVAL.value
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "ASK_USER"


async def test_negotiating_to_outside_authority_on_escalated_status(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        status=NegStatus.ESCALATED.value,
        escalation_trigger="deposit_or_payment_request",
        answered_at=NOW().isoformat(),
        end_call_requested=True,
    )
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.AWAITING_APPROVAL.value
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert "deposit" in rec.reason


async def test_negotiating_to_unavailable_when_provider_not_available(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session, active=False)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        status=NegStatus.UNAVAILABLE.value,
        answered_at=NOW().isoformat(),
        end_call_requested=True,
    )
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.FAILED.value  # cascaded, no other provider
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "DECLINE"


# --- on_call_ended, dropped: within cap / needing approval / no offer -------------------------


async def test_dropped_with_final_offer_within_cap_and_hours_known(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(
        session, tx, provider, answered_at=NOW().isoformat()
    )  # still IN_PROGRESS
    make_offer(session, negotiation, amount=18000, coverage_hours=4)
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.RESULT_READY.value
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "ACCEPT"
    assert "dropped" in rec.reason


async def test_dropped_with_final_offer_needing_approval(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider, answered_at=NOW().isoformat())
    make_offer(session, negotiation, amount=23000, coverage_hours=4)  # over cap
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.AWAITING_APPROVAL.value
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "ASK_USER"


async def test_dropped_with_no_offer_on_first_dial_attempt_redials(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(
        session, tx, provider, provider_call_id="CA1", answered_at=NOW().isoformat(), dial_attempt=1
    )
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert negotiation.dial_attempt == 2
    assert tx.status == TxStatus.CALLING.value  # redialed the same provider
    assert telephony.placed_calls == [provider.phone]


async def test_dropped_with_no_offer_after_the_redial_goes_unavailable(session):
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session, active=False)
    negotiation = make_negotiation(
        session, tx, provider, provider_call_id="CA1", answered_at=NOW().isoformat(), dial_attempt=2
    )
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.FAILED.value  # cascaded from UNAVAILABLE, no other provider
    rec = session.scalar(select(Recommendation).where(Recommendation.transaction_id == tx.id))
    assert rec.recommendation == "DECLINE"


async def test_silence_goodbye_is_treated_as_a_drop_with_no_offer(session):
    """CallCoordinator's own silence-timeout goodbye never calls a tool, so the negotiation is
    still IN_PROGRESS with end_call_requested still False -- indistinguishable from a real drop,
    which is exactly how contract 2 lists it alongside "dropped with no offer"."""
    tx = make_tx(session, status=TxStatus.NEGOTIATING.value)
    provider = make_provider(session, active=False)
    negotiation = make_negotiation(
        session, tx, provider, provider_call_id="CA1", answered_at=NOW().isoformat(), dial_attempt=2
    )
    telephony = FakeTelephonyProvider()

    await on_call_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.FAILED.value


# --- approve / decline --------------------------------------------------------------------------


async def test_result_ready_to_approved_records_approval(session):
    tx = make_tx(session, status=TxStatus.RESULT_READY.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=18000, coverage_hours=4)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="WITHIN_LIMIT",
            recommendation="ACCEPT",
            reason="ok",
        )
    )
    session.commit()

    approve(session, tx, offer.id)

    assert tx.status == TxStatus.APPROVED.value
    row = session.scalar(select(Approval).where(Approval.transaction_id == tx.id))
    assert row.decision == "APPROVED"
    assert row.offer_id == offer.id


async def test_awaiting_approval_to_approved(session):
    tx = make_tx(session, status=TxStatus.AWAITING_APPROVAL.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=23000)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="REQUIRES_APPROVAL",
            recommendation="ASK_USER",
            reason="over cap",
        )
    )
    session.commit()

    approve(session, tx, offer.id)

    assert tx.status == TxStatus.APPROVED.value


async def test_approve_with_stale_offer_id_raises_and_writes_no_approval(session):
    tx = make_tx(session, status=TxStatus.RESULT_READY.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=18000, coverage_hours=4)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="WITHIN_LIMIT",
            recommendation="ACCEPT",
            reason="ok",
        )
    )
    session.commit()

    with pytest.raises(StaleOffer):
        approve(session, tx, offer.id + 999)

    assert tx.status == TxStatus.RESULT_READY.value  # unchanged
    assert session.scalars(select(Approval)).all() == []


async def test_result_ready_to_declined_to_closed_immediately(session):
    tx = make_tx(session, status=TxStatus.RESULT_READY.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=18000, coverage_hours=4)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="WITHIN_LIMIT",
            recommendation="ACCEPT",
            reason="ok",
        )
    )
    session.commit()

    decline(session, tx)

    assert tx.status == TxStatus.CLOSED.value


async def test_awaiting_approval_to_declined_to_closed(session):
    tx = make_tx(session, status=TxStatus.AWAITING_APPROVAL.value)
    decline(session, tx)
    assert tx.status == TxStatus.CLOSED.value


async def test_decline_with_no_recommendation_writes_no_approval_row(session):
    tx = make_tx(session, status=TxStatus.AWAITING_APPROVAL.value)

    decline(session, tx)  # no Recommendation row at all is a defensive edge case, not reachable
    # in practice (AWAITING_APPROVAL always follows a written recommendation) but must not crash.

    assert tx.status == TxStatus.CLOSED.value
    assert session.scalars(select(Approval)).all() == []


# --- confirmation call flow (stretch) ------------------------------------------------------------


async def _approved_tx(session):
    tx = make_tx(session, status=TxStatus.APPROVED.value)
    provider = make_provider(session)
    tx.current_provider_id = provider.id
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=18000, coverage_hours=4)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="WITHIN_LIMIT",
            recommendation="ACCEPT",
            reason="ok",
        )
    )
    session.add(Approval(transaction_id=tx.id, offer_id=offer.id, decision="APPROVED"))
    session.commit()
    return tx, provider


async def test_start_confirmation_requires_an_approved_approval_row(session):
    tx = make_tx(session, status=TxStatus.APPROVED.value)
    provider = make_provider(session)
    tx.current_provider_id = provider.id
    negotiation = make_negotiation(session, tx, provider)
    offer = make_offer(session, negotiation, amount=18000, coverage_hours=4)
    session.add(
        Recommendation(
            transaction_id=tx.id,
            offer_id=offer.id,
            provider_id=provider.id,
            policy_status="WITHIN_LIMIT",
            recommendation="ACCEPT",
            reason="ok",
        )
    )
    session.commit()  # no Approval row written

    with pytest.raises(ApprovalRequired):
        await start_confirmation(
            session, tx, telephony=FakeTelephonyProvider(), max_calls_per_day=40, now=NOW
        )

    assert tx.status == TxStatus.APPROVED.value  # unchanged
    assert (
        session.scalars(select(Negotiation).where(Negotiation.kind == "confirmation")).all() == []
    )


async def test_approved_to_confirming_and_dials(session):
    tx, provider = await _approved_tx(session)
    telephony = FakeTelephonyProvider()

    negotiation = await start_confirmation(
        session, tx, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.CONFIRMING.value
    assert negotiation.kind == "confirmation"
    assert negotiation.provider_call_id is not None


async def test_confirming_to_confirmed(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        kind="confirmation",
        status=NegStatus.CONFIRMED.value,
        answered_at=NOW().isoformat(),
    )
    tx.status = TxStatus.CONFIRMING.value
    session.commit()

    await on_call_ended(
        session, tx, negotiation, telephony=FakeTelephonyProvider(), max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.CONFIRMED.value


async def test_confirming_to_confirmation_failed_on_decline(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        kind="confirmation",
        status=NegStatus.DECLINED_BY_PROVIDER.value,
        answered_at=NOW().isoformat(),
    )
    tx.status = TxStatus.CONFIRMING.value
    session.commit()

    await on_call_ended(
        session, tx, negotiation, telephony=FakeTelephonyProvider(), max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.CONFIRMATION_FAILED.value


async def test_confirming_to_agreed_within_policy_on_changed_terms_within_cap(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        kind="confirmation",
        status=NegStatus.TERMS_CHANGED.value,
        answered_at=NOW().isoformat(),
    )
    make_offer(session, negotiation, amount=17000, coverage_hours=4, source="confirmation_call")
    tx.status = TxStatus.CONFIRMING.value
    session.commit()

    await on_call_ended(
        session, tx, negotiation, telephony=FakeTelephonyProvider(), max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.RESULT_READY.value  # cascaded via write_recommendation(ACCEPT)


async def test_confirming_to_outside_authority_on_changed_terms_over_cap(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(
        session,
        tx,
        provider,
        kind="confirmation",
        status=NegStatus.TERMS_CHANGED.value,
        answered_at=NOW().isoformat(),
    )
    make_offer(session, negotiation, amount=25000, coverage_hours=4, source="confirmation_call")
    tx.status = TxStatus.CONFIRMING.value
    session.commit()

    await on_call_ended(
        session, tx, negotiation, telephony=FakeTelephonyProvider(), max_calls_per_day=40, now=NOW
    )

    assert tx.status == TxStatus.AWAITING_APPROVAL.value


async def test_confirming_to_confirm_retry_wait_and_back(session):
    tx, provider = await _approved_tx(session)
    tx.status = TxStatus.CONFIRMING.value
    session.commit()

    schedule_confirmation_retry(session, tx, delay_seconds=120)
    assert tx.status == TxStatus.CONFIRM_RETRY_WAIT.value

    negotiation = make_negotiation(session, tx, provider, kind="confirmation")
    telephony = FakeTelephonyProvider()
    await retry_confirmation(session, tx, negotiation, telephony=telephony)

    assert tx.status == TxStatus.CONFIRMING.value
    assert telephony.placed_calls == [provider.phone]


async def test_confirmation_failed_to_confirming_via_retry(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(session, tx, provider, kind="confirmation")
    tx.status = TxStatus.CONFIRMATION_FAILED.value
    session.commit()

    await retry_confirmation(session, tx, negotiation, telephony=FakeTelephonyProvider())

    assert tx.status == TxStatus.CONFIRMING.value


async def test_confirming_to_confirmation_failed_after_two_telephony_errors(session):
    tx, provider = await _approved_tx(session)
    negotiation = make_negotiation(session, tx, provider, kind="confirmation")
    tx.status = TxStatus.CONFIRM_RETRY_WAIT.value
    session.commit()

    await retry_confirmation(session, tx, negotiation, telephony=FailingTelephony(fail_times=2))

    assert tx.status == TxStatus.CONFIRMATION_FAILED.value


# --- callback -> orchestrator mapping ------------------------------------------------------------


async def test_resolve_negotiation_retries_through_the_placing_transactions_commit_race(
    session_factory,
):
    with session_factory() as setup_db:
        tx = make_tx(setup_db)
        provider = make_provider(setup_db)
        setup_db.commit()

    sleeps: list[float] = []

    def commit_negotiation_on_second_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:  # simulates place_call's commit landing mid-retry
            with session_factory() as writer:
                writer.add(
                    Negotiation(
                        transaction_id=tx.id,
                        provider_id=provider.id,
                        kind="negotiation",
                        provider_call_id="CA1",
                    )
                )
                writer.commit()

    resolved = resolve_negotiation(
        session_factory,
        "CA1",
        attempts=5,
        delay_seconds=0.0,
        sleep=commit_negotiation_on_second_sleep,
    )

    assert resolved is not None
    db, negotiation = resolved
    assert negotiation.provider_call_id == "CA1"
    assert len(sleeps) == 2
    db.close()


async def test_resolve_negotiation_gives_up_after_the_retry_window(session_factory):
    resolved = resolve_negotiation(
        session_factory, "no-such-call", attempts=3, delay_seconds=0.0, sleep=lambda _s: None
    )
    assert resolved is None


async def test_unrecognized_call_agent_rejects_and_audits_without_a_transaction(session):
    agent = UnrecognizedCallAgent(lambda: session)

    turn = await agent.start()

    assert turn.end_call is True
    assert "doesn't take incoming calls" in turn.say
    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "error"))
    assert event is not None
    assert event.transaction_id is None


class _StubInnerAgent:
    def __init__(self, turns):
        self._turns = list(turns)
        self.finished = []

    async def start(self):
        return self._turns.pop(0)

    async def respond(self, _message):
        return self._turns.pop(0)

    async def finish(self, reason):
        self.finished.append(reason)


async def test_orchestrated_call_tracks_end_call_and_drives_on_call_ended(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    provider = make_provider(session)
    negotiation = make_negotiation(session, tx, provider, provider_call_id="CA1")
    inner = _StubInnerAgent([AgentTurn("hi"), AgentTurn("bye", end_call=True)])
    telephony = FakeTelephonyProvider()
    wrapped = OrchestratedCall(
        inner, session, tx, negotiation, telephony=telephony, max_calls_per_day=40, now=NOW
    )

    await wrapped.start()
    assert tx.status == TxStatus.NEGOTIATING.value  # on_call_answered ran before the inner start()
    assert negotiation.answered_at is not None

    await wrapped.respond(None)
    assert negotiation.end_call_requested is True  # the "bye" turn had end_call=True

    await wrapped.finish("completed")
    assert inner.finished == ["completed"]
    # No offer was ever recorded -> a drop, dial_attempt still 1 -> redialed the same provider.
    assert tx.status == TxStatus.CALLING.value
    assert telephony.placed_calls == [provider.phone]


# --- misc audit / helper coverage -----------------------------------------------------------------


async def test_advance_from_unavailable_stops_cleanly_when_guard_blocks_mid_cascade(session):
    tx = make_tx(session, status=TxStatus.CALLING.value)
    first = make_provider(session, priority=1)
    second = make_provider(session, name="Studio B", phone="+254711000002", priority=2)
    make_negotiation(session, tx, first)
    other_tx = make_tx(session)
    make_negotiation(session, other_tx, second, created_at=NOW().isoformat(timespec="milliseconds"))

    transition(session, tx, TxStatus.UNAVAILABLE.value, "test setup")
    session.commit()
    telephony = FakeTelephonyProvider()

    await advance_from_unavailable(session, tx, telephony=telephony, max_calls_per_day=1, now=NOW)

    assert tx.status == TxStatus.UNAVAILABLE.value  # stuck, not crashed: guard blocked the retry
    assert telephony.placed_calls == []


async def test_record_event_helper_is_reused_for_provider_selection(session):
    tx = make_tx(session)
    make_provider(session)
    select_provider(session, tx)
    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "provider.selected"))
    assert event is not None
    assert event.payload["name"] == "Studio A"
