"""Transaction orchestrator (epic #10, contracts 2 and 4).

Pure Python, no vendor SDK imports: telephony and the LLM arrive as injected dependencies
(`TelephonyProvider`, and indirectly through the call factory built in `app/main.py`), the same
pattern as `app/calls.py`. This module owns every transition in `app/states.py` and is the single
place that decides what happens next after a call places, answers, or ends.
"""

import logging
import time as time_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.conversation import AgentTurn, CallerMessage, ConversationAgent
from app.models import Approval, Negotiation, Offer, Provider, Recommendation, Transaction
from app.policy import (
    OfferTerms,
    ProviderOutcome,
    RecommendationKind,
    build_recommendation,
)
from app.states import NegStatus, TxStatus, transition
from app.telephony.base import TelephonyError, TelephonyProvider

log = logging.getLogger(__name__)

NAIROBI = ZoneInfo("Africa/Nairobi")

REJECTION_SAY = "Sorry, this line doesn't take incoming calls. Goodbye."


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CallGuardBlocked(Exception):
    """Settings.max_calls_per_day negotiations already created for today (Africa/Nairobi)."""

    def __init__(self, max_calls_per_day: int) -> None:
        super().__init__(f"Daily call limit reached ({max_calls_per_day})")
        self.max_calls_per_day = max_calls_per_day


class StaleOffer(Exception):
    """approve()'s offer_id doesn't match the transaction's current recommendation."""


class ApprovalRequired(Exception):
    """start_confirmation() with no APPROVED approvals row for the offer being confirmed."""


# --- provider selection -------------------------------------------------------------------------


def _next_untried_provider(session: Session, tx: Transaction) -> Provider | None:
    tried_ids = set(
        session.scalars(
            select(Negotiation.provider_id).where(
                Negotiation.transaction_id == tx.id, Negotiation.kind == "negotiation"
            )
        )
    )
    providers = session.scalars(
        select(Provider).where(Provider.active.is_(True)).order_by(Provider.priority)
    ).all()
    return next((p for p in providers if p.id not in tried_ids), None)


def select_provider(session: Session, tx: Transaction) -> Provider | None:
    """CREATED -> PROVIDER_SELECTED/FAILED, or NEXT_PROVIDER -> PROVIDER_SELECTED.

    Picks the lowest-priority active provider not yet tried for this transaction. Returns None
    (transaction moved to FAILED) only when called from CREATED with no active providers at all;
    contract 2 guarantees NEXT_PROVIDER is only entered when a candidate exists.
    """
    candidate = _next_untried_provider(session, tx)
    if candidate is None:
        transition(session, tx, TxStatus.FAILED.value, "no active providers")
        session.commit()
        return None
    transition(session, tx, TxStatus.PROVIDER_SELECTED.value, f"selected provider {candidate.id}")
    tx.current_provider_id = candidate.id
    record_event(
        session, tx.id, "provider.selected", {"provider_id": candidate.id, "name": candidate.name}
    )
    session.commit()
    return candidate


# --- call guard -----------------------------------------------------------------------------


def _nairobi_day_bounds_utc(moment: datetime) -> tuple[str, str]:
    local = moment.astimezone(NAIROBI)
    start_local = datetime.combine(local.date(), dtime.min, tzinfo=NAIROBI)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(UTC).isoformat(timespec="milliseconds"),
        end_local.astimezone(UTC).isoformat(timespec="milliseconds"),
    )


def _calls_today(session: Session, moment: datetime) -> int:
    start, end = _nairobi_day_bounds_utc(moment)
    return (
        session.scalar(
            select(func.count())
            .select_from(Negotiation)
            .where(Negotiation.created_at >= start, Negotiation.created_at < end)
        )
        or 0
    )


def _check_call_guard(
    session: Session, tx: Transaction, max_calls_per_day: int, now: Callable[[], datetime]
) -> None:
    count = _calls_today(session, now())
    if count >= max_calls_per_day:
        record_event(session, tx.id, "call.guard_blocked", {"max_calls_per_day": max_calls_per_day})
        session.commit()
        raise CallGuardBlocked(max_calls_per_day)


# --- dialing --------------------------------------------------------------------------------


async def _dial(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    provider: Provider,
    *,
    telephony: TelephonyProvider,
) -> None:
    """Try `telephony.place_call` up to twice; the transaction ends up CALLING or UNAVAILABLE.

    Legal from either PROVIDER_SELECTED (a fresh dial) or NEGOTIATING (a same-provider redial
    after a drop) -- `transition()` validates whichever pair actually applies.
    """
    last_error: TelephonyError | None = None
    for attempt in range(2):
        try:
            placed = await telephony.place_call(provider.phone)
        except TelephonyError as exc:
            last_error = exc
            record_event(
                session,
                tx.id,
                "error",
                {"where": "place_call", "attempt": attempt + 1, "detail": str(exc)},
            )
            session.commit()
            continue
        negotiation.provider_call_id = placed.provider_call_id
        transition(session, tx, TxStatus.CALLING.value, "call placed")
        record_event(
            session, tx.id, "call.status", {"negotiation_id": negotiation.id, "status": "CALLING"}
        )
        session.commit()
        return

    negotiation.status = NegStatus.FAILED.value
    negotiation.end_reason = str(last_error) if last_error else "telephony error"
    transition(session, tx, TxStatus.UNAVAILABLE.value, "telephony error placing call twice")
    session.commit()


async def place_call(
    session: Session,
    tx: Transaction,
    provider: Provider,
    *,
    telephony: TelephonyProvider,
    max_calls_per_day: int,
    now: Callable[[], datetime] = _utcnow,
) -> Negotiation:
    """PROVIDER_SELECTED -> CALLING or UNAVAILABLE. Raises CallGuardBlocked before dialing if
    Settings.max_calls_per_day negotiations were already created today (Africa/Nairobi)."""
    _check_call_guard(session, tx, max_calls_per_day, now)
    negotiation = Negotiation(transaction_id=tx.id, provider_id=provider.id, kind="negotiation")
    session.add(negotiation)
    session.flush()
    record_event(
        session,
        tx.id,
        "call.created",
        {"negotiation_id": negotiation.id, "provider_id": provider.id},
    )
    session.commit()
    await _dial(session, tx, negotiation, provider, telephony=telephony)
    return negotiation


async def advance_from_unavailable(
    session: Session,
    tx: Transaction,
    *,
    telephony: TelephonyProvider,
    max_calls_per_day: int,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """UNAVAILABLE -> NEXT_PROVIDER -> PROVIDER_SELECTED -> (dial), looping over providers, or
    UNAVAILABLE -> FAILED with a DECLINE recommendation once none are left.

    A call-guard block mid-cascade (this runs outside any HTTP request, so there's nowhere to
    return a 429 to) just stops the cascade, leaving the transaction at UNAVAILABLE for a human
    to retry once the daily limit resets.
    """
    while tx.status == TxStatus.UNAVAILABLE.value:
        if _next_untried_provider(session, tx) is None:
            write_recommendation(session, tx, offer=None, provider=None, counteroffers_made=0)
            return
        try:
            _check_call_guard(session, tx, max_calls_per_day, now)
        except CallGuardBlocked:
            return
        transition(session, tx, TxStatus.NEXT_PROVIDER.value, "trying next provider")
        session.commit()
        provider = select_provider(session, tx)
        assert provider is not None  # guaranteed by the _next_untried_provider check above
        negotiation = Negotiation(transaction_id=tx.id, provider_id=provider.id, kind="negotiation")
        session.add(negotiation)
        session.flush()
        record_event(
            session,
            tx.id,
            "call.created",
            {"negotiation_id": negotiation.id, "provider_id": provider.id},
        )
        session.commit()
        await _dial(session, tx, negotiation, provider, telephony=telephony)


# --- offer/recommendation helpers ------------------------------------------------------------


def _latest_offer(session: Session, negotiation_id: str) -> Offer | None:
    return session.scalar(
        select(Offer)
        .where(Offer.negotiation_id == negotiation_id)
        .order_by(Offer.id.desc())
        .limit(1)
    )


def _latest_recommendation(session: Session, tx: Transaction) -> Recommendation | None:
    return session.scalar(
        select(Recommendation)
        .where(Recommendation.transaction_id == tx.id)
        .order_by(Recommendation.id.desc())
        .limit(1)
    )


def _offer_terms(offer: Offer | None) -> OfferTerms | None:
    if offer is None:
        return None
    return OfferTerms(
        available=offer.available,
        amount=offer.amount,
        coverage_hours=offer.coverage_hours,
        deposit_required=offer.deposit_required,
        provider_refuses_negotiation=offer.provider_refuses_negotiation,
    )


_OUTCOME_BY_NEG_STATUS = {
    NegStatus.NO_ANSWER.value: ProviderOutcome.NO_ANSWER,
    NegStatus.FAILED.value: ProviderOutcome.NOT_AVAILABLE,
    NegStatus.UNAVAILABLE.value: ProviderOutcome.NOT_AVAILABLE,
}


def _provider_outcomes(
    session: Session, tx: Transaction
) -> tuple[tuple[str, ProviderOutcome], ...]:
    rows = session.execute(
        select(Negotiation, Provider.name)
        .join(Provider, Provider.id == Negotiation.provider_id)
        .where(Negotiation.transaction_id == tx.id, Negotiation.kind == "negotiation")
        .order_by(Negotiation.created_at)
    ).all()
    return tuple(
        (name, _OUTCOME_BY_NEG_STATUS.get(neg.status, ProviderOutcome.CALL_DROPPED))
        for neg, name in rows
    )


def write_recommendation(
    session: Session,
    tx: Transaction,
    *,
    offer: Offer | None,
    provider: Provider | None,
    counteroffers_made: int,
    escalation_trigger: str | None = None,
    call_dropped: bool = False,
) -> Recommendation:
    """AGREED_WITHIN_POLICY->RESULT_READY (ACCEPT), OUTSIDE_AUTHORITY->AWAITING_APPROVAL
    (ASK_USER), or UNAVAILABLE->FAILED (DECLINE) -- whichever `tx.status` is already at."""
    offer_terms = _offer_terms(offer)
    outcomes = () if offer_terms is not None else _provider_outcomes(session, tx)
    result = build_recommendation(
        cap=tx.max_budget,
        service_date=date.fromisoformat(tx.service_date),
        provider_name=provider.name if provider else None,
        final_offer=offer_terms,
        counteroffers_made=counteroffers_made,
        escalation_trigger=escalation_trigger,
        call_dropped=call_dropped,
        provider_outcomes=outcomes,
    )
    rec = Recommendation(
        transaction_id=tx.id,
        offer_id=offer.id if offer else None,
        provider_id=provider.id if provider else None,
        policy_status=result.policy_status.value,
        recommendation=result.recommendation.value,
        reason=result.reason,
    )
    session.add(rec)
    session.flush()
    record_event(
        session,
        tx.id,
        "recommendation.created",
        {
            "recommendation_id": rec.id,
            "policy_status": rec.policy_status,
            "recommendation": rec.recommendation,
        },
    )

    target = {
        RecommendationKind.ACCEPT.value: TxStatus.RESULT_READY,
        RecommendationKind.ASK_USER.value: TxStatus.AWAITING_APPROVAL,
        RecommendationKind.DECLINE.value: TxStatus.FAILED,
    }[rec.recommendation]
    transition(session, tx, target.value, f"recommendation {rec.recommendation}")
    session.commit()
    return rec


# --- call lifecycle callbacks -----------------------------------------------------------------


def on_call_answered(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """CALLING -> NEGOTIATING on the first CallAnswered for this session; a repeat is a no-op
    (mirrors CallCoordinator treating a repeat "answered" callback as silence, not a restart)."""
    if negotiation.answered_at is not None:
        return
    negotiation.status = NegStatus.IN_PROGRESS.value
    negotiation.answered_at = now().isoformat(timespec="milliseconds")
    record_event(session, tx.id, "call.answered", {"negotiation_id": negotiation.id})
    if tx.status == TxStatus.CALLING.value:
        transition(session, tx, TxStatus.NEGOTIATING.value, "call answered")
    session.commit()


async def _handle_answered_negotiation_ended(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    *,
    telephony: TelephonyProvider,
    max_calls_per_day: int,
    now: Callable[[], datetime],
) -> None:
    status = negotiation.status
    provider = session.get(Provider, negotiation.provider_id)
    latest = _latest_offer(session, negotiation.id)

    if status == NegStatus.AGREED.value:
        transition(session, tx, TxStatus.AGREED_WITHIN_POLICY.value, "negotiation agreed")
        session.commit()
        write_recommendation(
            session,
            tx,
            offer=latest,
            provider=provider,
            counteroffers_made=negotiation.attempt_count,
        )
        return

    if status in (NegStatus.OUTSIDE_AUTHORITY.value, NegStatus.ESCALATED.value):
        transition(session, tx, TxStatus.OUTSIDE_AUTHORITY.value, f"negotiation {status.lower()}")
        session.commit()
        write_recommendation(
            session,
            tx,
            offer=latest,
            provider=provider,
            counteroffers_made=negotiation.attempt_count,
            escalation_trigger=negotiation.escalation_trigger
            if status == NegStatus.ESCALATED.value
            else None,
        )
        return

    if status == NegStatus.UNAVAILABLE.value:
        transition(session, tx, TxStatus.UNAVAILABLE.value, "provider not available")
        session.commit()
        await advance_from_unavailable(
            session, tx, telephony=telephony, max_calls_per_day=max_calls_per_day, now=now
        )
        return

    # Still IN_PROGRESS (or earlier): the call ended without a definitive tool outcome -- a drop,
    # or CallCoordinator's own silence-timeout goodbye (never routed through a tool call either).
    if latest is not None and latest.amount is not None and latest.available:
        within_cap = latest.amount <= tx.max_budget and latest.coverage_hours is not None
        target = TxStatus.AGREED_WITHIN_POLICY if within_cap else TxStatus.OUTSIDE_AUTHORITY
        transition(session, tx, target.value, "call dropped with a final offer")
        session.commit()
        write_recommendation(
            session,
            tx,
            offer=latest,
            provider=provider,
            counteroffers_made=negotiation.attempt_count,
            call_dropped=True,
        )
        return

    if negotiation.dial_attempt == 1:
        negotiation.dial_attempt = 2
        record_event(session, tx.id, "call.redial", {"negotiation_id": negotiation.id})
        session.commit()
        await _dial(session, tx, negotiation, provider, telephony=telephony)
        return

    transition(session, tx, TxStatus.UNAVAILABLE.value, "dropped with no offer after the redial")
    session.commit()
    await advance_from_unavailable(
        session, tx, telephony=telephony, max_calls_per_day=max_calls_per_day, now=now
    )


async def on_call_ended(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    *,
    telephony: TelephonyProvider,
    max_calls_per_day: int,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Derives the outcome from DB state and drives every downstream transition/recommendation."""
    if negotiation.kind == "confirmation":
        await _on_confirmation_call_ended(session, tx, negotiation, telephony=telephony, now=now)
        return

    if negotiation.answered_at is None:
        negotiation.status = (
            NegStatus.NO_ANSWER.value
            if negotiation.end_reason == "no-answer"
            else NegStatus.FAILED.value
        )
        transition(session, tx, TxStatus.UNAVAILABLE.value, "call ended before it was answered")
        session.commit()
        await advance_from_unavailable(
            session, tx, telephony=telephony, max_calls_per_day=max_calls_per_day, now=now
        )
        return

    await _handle_answered_negotiation_ended(
        session, tx, negotiation, telephony=telephony, max_calls_per_day=max_calls_per_day, now=now
    )


# --- approve / decline -----------------------------------------------------------------------


def approve(session: Session, tx: Transaction, offer_id: int) -> None:
    """RESULT_READY/AWAITING_APPROVAL -> APPROVED. Raises StaleOffer (no DB write) unless
    `offer_id` matches the transaction's current recommendation."""
    rec = _latest_recommendation(session, tx)
    if rec is None or rec.offer_id != offer_id:
        raise StaleOffer(f"offer_id {offer_id} does not match the current recommendation")
    transition(session, tx, TxStatus.APPROVED.value, "approved by user")
    session.add(Approval(transaction_id=tx.id, offer_id=offer_id, decision="APPROVED"))
    record_event(
        session, tx.id, "approval.recorded", {"offer_id": offer_id, "decision": "APPROVED"}
    )
    session.commit()


def decline(session: Session, tx: Transaction) -> None:
    """RESULT_READY/AWAITING_APPROVAL -> DECLINED -> CLOSED, immediately."""
    rec = _latest_recommendation(session, tx)
    transition(session, tx, TxStatus.DECLINED.value, "declined by user")
    if rec is not None and rec.offer_id is not None:
        session.add(Approval(transaction_id=tx.id, offer_id=rec.offer_id, decision="DECLINED"))
    record_event(
        session,
        tx.id,
        "approval.recorded",
        {"decision": "DECLINED", "offer_id": rec.offer_id if rec else None},
    )
    transition(session, tx, TxStatus.CLOSED.value, "closed after decline")
    session.commit()


# --- confirmation call flow (stretch) ----------------------------------------------------------


async def _dial_confirmation(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    provider: Provider,
    *,
    telephony: TelephonyProvider,
) -> None:
    last_error: TelephonyError | None = None
    for attempt in range(2):
        try:
            placed = await telephony.place_call(provider.phone)
        except TelephonyError as exc:
            last_error = exc
            record_event(
                session,
                tx.id,
                "error",
                {"where": "place_call", "attempt": attempt + 1, "detail": str(exc)},
            )
            session.commit()
            continue
        negotiation.provider_call_id = placed.provider_call_id
        record_event(
            session,
            tx.id,
            "call.status",
            {"negotiation_id": negotiation.id, "status": "CONFIRMING"},
        )
        session.commit()
        return

    negotiation.status = NegStatus.FAILED.value
    negotiation.end_reason = str(last_error) if last_error else "telephony error"
    transition(
        session,
        tx,
        TxStatus.CONFIRMATION_FAILED.value,
        "telephony error placing confirmation call twice",
    )
    session.commit()


async def start_confirmation(
    session: Session,
    tx: Transaction,
    *,
    telephony: TelephonyProvider,
    max_calls_per_day: int,
    now: Callable[[], datetime] = _utcnow,
) -> Negotiation:
    """APPROVED -> CONFIRMING. Never dials without an approvals row APPROVED for the current
    recommendation's offer_id (acceptance criterion 3)."""
    rec = _latest_recommendation(session, tx)
    approved = (
        rec is not None
        and rec.offer_id is not None
        and (
            session.scalar(
                select(Approval.id).where(
                    Approval.transaction_id == tx.id,
                    Approval.offer_id == rec.offer_id,
                    Approval.decision == "APPROVED",
                )
            )
            is not None
        )
    )
    if not approved:
        raise ApprovalRequired("no APPROVED approvals row for the current recommendation's offer")

    transition(session, tx, TxStatus.CONFIRMING.value, "starting confirmation call")
    provider = session.get(Provider, tx.current_provider_id)
    assert provider is not None
    negotiation = Negotiation(transaction_id=tx.id, provider_id=provider.id, kind="confirmation")
    session.add(negotiation)
    session.flush()
    record_event(
        session,
        tx.id,
        "call.created",
        {"negotiation_id": negotiation.id, "provider_id": provider.id, "kind": "confirmation"},
    )
    session.commit()
    _check_call_guard(session, tx, max_calls_per_day, now)
    await _dial_confirmation(session, tx, negotiation, provider, telephony=telephony)
    return negotiation


async def _on_confirmation_call_ended(
    session: Session,
    tx: Transaction,
    negotiation: Negotiation,
    *,
    telephony: TelephonyProvider,
    now: Callable[[], datetime],
) -> None:
    provider = session.get(Provider, negotiation.provider_id)
    assert provider is not None

    if negotiation.answered_at is None:
        if negotiation.dial_attempt == 1:
            negotiation.dial_attempt = 2
            record_event(session, tx.id, "call.redial", {"negotiation_id": negotiation.id})
            session.commit()
            await _dial_confirmation(session, tx, negotiation, provider, telephony=telephony)
            return
        transition(
            session, tx, TxStatus.CONFIRMATION_FAILED.value, "no answer on confirmation call"
        )
        session.commit()
        return

    status = negotiation.status
    if status == NegStatus.CONFIRMED.value:
        transition(session, tx, TxStatus.CONFIRMED.value, "provider confirmed")
        session.commit()
        return

    if status == NegStatus.TERMS_CHANGED.value:
        latest = _latest_offer(session, negotiation.id)
        within_cap = (
            latest is not None
            and latest.amount is not None
            and latest.amount <= tx.max_budget
            and latest.coverage_hours is not None
        )
        target = TxStatus.AGREED_WITHIN_POLICY if within_cap else TxStatus.OUTSIDE_AUTHORITY
        transition(session, tx, target.value, "provider changed terms on confirmation call")
        session.commit()
        write_recommendation(session, tx, offer=latest, provider=provider, counteroffers_made=0)
        return

    if status == NegStatus.DECLINED_BY_PROVIDER.value:
        transition(session, tx, TxStatus.CONFIRMATION_FAILED.value, "provider no longer available")
        session.commit()
        return

    # Dropped with no definitive confirmation outcome.
    if negotiation.dial_attempt == 1 and not negotiation.end_call_requested:
        negotiation.dial_attempt = 2
        record_event(session, tx.id, "call.redial", {"negotiation_id": negotiation.id})
        session.commit()
        await _dial_confirmation(session, tx, negotiation, provider, telephony=telephony)
        return

    transition(session, tx, TxStatus.CONFIRMATION_FAILED.value, "confirmation call dropped")
    session.commit()


def schedule_confirmation_retry(
    session: Session,
    tx: Transaction,
    *,
    delay_seconds: int,
    scheduler: Callable[[float, Callable[[], object]], object] | None = None,
) -> None:
    """CONFIRMING/CONFIRMATION_FAILED -> CONFIRM_RETRY_WAIT, and hand `delay_seconds` plus a
    no-arg callback to `scheduler` (defaults to a no-op: the caller/tests drive the actual
    retry_confirmation() call; production wiring can pass asyncio's loop timer)."""
    transition(session, tx, TxStatus.CONFIRM_RETRY_WAIT.value, "confirmation retry scheduled")
    record_event(session, tx.id, "confirmation.retry_scheduled", {"delay_seconds": delay_seconds})
    session.commit()
    if scheduler is not None:
        scheduler(delay_seconds, lambda: None)


async def retry_confirmation(
    session: Session, tx: Transaction, negotiation: Negotiation, *, telephony: TelephonyProvider
) -> None:
    """CONFIRM_RETRY_WAIT or CONFIRMATION_FAILED -> CONFIRMING, then redial."""
    transition(session, tx, TxStatus.CONFIRMING.value, "retrying confirmation call")
    session.commit()
    provider = session.get(Provider, negotiation.provider_id)
    assert provider is not None
    await _dial_confirmation(session, tx, negotiation, provider, telephony=telephony)


# --- callback -> orchestrator wiring (used by app/main.py) --------------------------------------


def resolve_negotiation(
    session_factory: Callable[[], Session],
    provider_call_id: str,
    *,
    attempts: int = 5,
    delay_seconds: float = 0.2,
    sleep: Callable[[float], None] = time_module.sleep,
) -> tuple[Session, Negotiation] | None:
    """Resolve a telephony session id to its Negotiation, retrying briefly for the race where a
    callback beats `place_call`'s own commit. Returns an open Session the caller now owns (close
    it when done), or None if no negotiation ever showed up."""
    db = session_factory()
    for attempt in range(attempts):
        negotiation = db.scalar(
            select(Negotiation).where(Negotiation.provider_call_id == provider_call_id)
        )
        if negotiation is not None:
            return db, negotiation
        if attempt < attempts - 1:
            sleep(delay_seconds)
    db.close()
    return None


class UnrecognizedCallAgent:
    """A call sid matching no negotiation, after the retry window in `resolve_negotiation`:
    a polite rejection, covering both a genuinely unknown inbound call and a lookup race that
    outlasted the retry window."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    async def start(self) -> AgentTurn:
        return self._reject()

    async def respond(self, _message: CallerMessage) -> AgentTurn:
        return self._reject()

    async def finish(self, _reason: str | None) -> None:
        return None

    def _reject(self) -> AgentTurn:
        with self._session_factory() as db:
            record_event(db, None, "error", {"detail": "no negotiation for this call"})
            db.commit()
        return AgentTurn(REJECTION_SAY, end_call=True)


@dataclass
class OrchestratedCall:
    """Wraps a call's `ConversationAgent` with the orchestrator hooks #6 owns: `on_call_answered`
    before the first turn, `end_call_requested` tracking on every turn (contract 2's definition of
    a "drop"), and `on_call_ended` once the call finishes. `CallCoordinator` (app/calls.py) stays
    provider- and orchestrator-agnostic; this is the one place that bridges the two.
    """

    inner: ConversationAgent
    session: Session
    tx: Transaction
    negotiation: Negotiation
    telephony: TelephonyProvider
    max_calls_per_day: int
    now: Callable[[], datetime] = _utcnow

    async def start(self) -> AgentTurn:
        on_call_answered(self.session, self.tx, self.negotiation, now=self.now)
        return self._track(await self.inner.start())

    async def respond(self, message: CallerMessage) -> AgentTurn:
        return self._track(await self.inner.respond(message))

    async def finish(self, reason: str | None) -> None:
        await self.inner.finish(reason)
        await on_call_ended(
            self.session,
            self.tx,
            self.negotiation,
            telephony=self.telephony,
            max_calls_per_day=self.max_calls_per_day,
            now=self.now,
        )

    def _track(self, turn: AgentTurn) -> AgentTurn:
        if turn.end_call and not self.negotiation.end_call_requested:
            self.negotiation.end_call_requested = True
            self.session.commit()
        return turn


__all__: Sequence[str] = (
    "ApprovalRequired",
    "CallGuardBlocked",
    "OrchestratedCall",
    "StaleOffer",
    "UnrecognizedCallAgent",
    "advance_from_unavailable",
    "approve",
    "decline",
    "on_call_answered",
    "on_call_ended",
    "place_call",
    "resolve_negotiation",
    "retry_confirmation",
    "schedule_confirmation_retry",
    "select_provider",
    "start_confirmation",
    "write_recommendation",
)
