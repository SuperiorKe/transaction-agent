"""Transaction API routes (epic #10, contract 4)."""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.models import (
    AuditEvent,
    Negotiation,
    Offer,
    Provider,
    Recommendation,
    Transaction,
    TranscriptTurn,
)
from app.orchestrator import (
    ApprovalRequired,
    CallGuardBlocked,
    StaleOffer,
    advance_from_unavailable,
    check_call_guard,
    maybe_timeout_confirming,
    place_call,
    retry_confirmation,
    select_provider,
    start_confirmation,
)
from app.orchestrator import (
    approve as orchestrator_approve,
)
from app.orchestrator import (
    decline as orchestrator_decline,
)
from app.schemas import (
    ApproveBody,
    AuditEventView,
    ErrorDetail,
    NegotiationView,
    OfferView,
    ProviderView,
    RecommendationView,
    TransactionCreate,
    TransactionView,
    TranscriptTurnView,
)
from app.states import TERMINAL_STATES, TxStatus
from app.telephony.base import TelephonyProvider

log = logging.getLogger(__name__)

# String membership, not `TxStatus(tx.status) in TERMINAL_STATES`: constructing the enum raises
# ValueError on any status value that isn't currently a member (a hand-edited row, or a DB file
# from before a status was removed -- CLAUDE.md's "delete the DB file after a schema change" is a
# recommendation, not a guarantee), which would 500 the one route the owner has to see a
# transaction at all instead of just reporting it as non-terminal.
_TERMINAL_VALUES = frozenset(status.value for status in TERMINAL_STATES)

ALLOWED_ACTIONS_BY_STATUS: dict[str, tuple[str, ...]] = {
    TxStatus.CREATED.value: ("start",),
    TxStatus.RESULT_READY.value: ("approve", "decline"),
    TxStatus.AWAITING_APPROVAL.value: ("approve", "decline"),
    # A recovery action for an exception between recording approval and the confirmation dial
    # (an unset current_provider_id, a DB error) -- anything but StaleOffer/CallGuardBlocked, which
    # approve_transaction already handles. See TODOS.md "Recover from CONFIRMING /
    # CONFIRMATION_FAILED...". retry_confirmation_transaction treats APPROVED as "start fresh"
    # rather than "resolve a confirmation negotiation to retry", since none exists yet.
    TxStatus.APPROVED.value: ("retry_confirmation",),
    # "decline" here means abandon: give up on this booking after a failed confirmation call
    # instead of retrying again. Reuses the ordinary decline()/DECLINED->CLOSED path.
    TxStatus.CONFIRMATION_FAILED.value: ("retry_confirmation", "decline"),
}


def _mask_phone(phone: str) -> str:
    """'+254712345678' -> '+254 7•• ••• 678'. Falls back to the raw value for anything that
    doesn't look like the Kenyan E.164 numbers `app/seed.py` validates on the way in."""
    if not phone.startswith("+254") or len(phone) != 13:
        return phone
    digits = phone[4:]
    return f"+254 {digits[0]}•• ••• {digits[-3:]}"


def _build_view(session: Session, tx: Transaction) -> TransactionView:
    provider_view = None
    if tx.current_provider_id is not None:
        provider = session.get(Provider, tx.current_provider_id)
        if provider is not None:
            provider_view = ProviderView(
                id=provider.id, name=provider.name, phone_masked=_mask_phone(provider.phone)
            )

    negotiations = []
    neg_rows = session.scalars(
        select(Negotiation)
        .where(Negotiation.transaction_id == tx.id)
        .order_by(Negotiation.created_at)
    ).all()
    for neg in neg_rows:
        neg_provider = session.get(Provider, neg.provider_id)
        transcript = session.scalars(
            select(TranscriptTurn)
            .where(TranscriptTurn.negotiation_id == neg.id)
            .order_by(TranscriptTurn.id)
        ).all()
        offers = session.scalars(
            select(Offer).where(Offer.negotiation_id == neg.id).order_by(Offer.id)
        ).all()
        negotiations.append(
            NegotiationView(
                id=neg.id,
                kind=neg.kind,
                provider_name=neg_provider.name if neg_provider else "",
                status=neg.status,
                attempt_count=neg.attempt_count,
                dial_attempt=neg.dial_attempt,
                answered_at=neg.answered_at,
                duration_seconds=neg.duration_seconds,
                transcript=[
                    TranscriptTurnView(speaker=t.speaker, text=t.text, at=t.created_at)
                    for t in transcript
                ],
                offers=[
                    OfferView(
                        id=o.id,
                        amount=o.amount,
                        available=o.available,
                        coverage_hours=o.coverage_hours,
                        deposit_required=o.deposit_required,
                        terms=o.terms,
                        decision=o.decision,
                        at=o.created_at,
                    )
                    for o in offers
                ],
            )
        )

    rec_row = session.scalar(
        select(Recommendation)
        .where(Recommendation.transaction_id == tx.id)
        .order_by(Recommendation.id.desc())
        .limit(1)
    )
    recommendation = None
    if rec_row is not None:
        offer = session.get(Offer, rec_row.offer_id) if rec_row.offer_id else None
        rec_provider = session.get(Provider, rec_row.provider_id) if rec_row.provider_id else None
        recommendation = RecommendationView(
            offer_id=rec_row.offer_id,
            provider_name=rec_provider.name if rec_provider else None,
            final_price=offer.amount if offer else None,
            policy_status=rec_row.policy_status,
            recommendation=rec_row.recommendation,
            reason=rec_row.reason,
        )

    audit_rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.transaction_id == tx.id)
        .order_by(AuditEvent.id.desc())
        .limit(50)
    ).all()

    # Uncapped and oldest first, from status.changed rows rather than the capped audit list: the
    # owner UI ticks timeline stages from this, and one real call writes far more than 50 events.
    status_events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.transaction_id == tx.id, AuditEvent.event_type == "status.changed")
        .order_by(AuditEvent.id)
    ).all()
    if status_events:
        # `.get()` rather than a bare subscript: every status.changed row app/states.py writes has
        # both keys, but a malformed one (a schema change, a hand-edited row, a test-only route)
        # must degrade the timeline, not 500 the one route the owner has to see the transaction at
        # all.
        first_from = status_events[0].payload.get("from")
        status_history = [first_from] if first_from is not None else []
        status_history += [event.payload["to"] for event in status_events if "to" in event.payload]
        if not status_history:
            status_history = [tx.status]
    else:
        status_history = [tx.status]

    allowed_actions = list(ALLOWED_ACTIONS_BY_STATUS.get(tx.status, ()))
    if recommendation is None or recommendation.offer_id is None:
        # An escalation reaches AWAITING_APPROVAL even when no price was ever quoted. With no
        # offer_id there is nothing approvable, so don't advertise an approve that can only 409.
        allowed_actions = [action for action in allowed_actions if action != "approve"]

    return TransactionView(
        id=tx.id,
        status=tx.status,
        terminal=tx.status in _TERMINAL_VALUES,
        status_history=status_history,
        request=tx.request,
        service=tx.service,
        service_date=tx.service_date,
        location=tx.location,
        max_budget=tx.max_budget,
        currency=tx.currency,
        max_attempts=tx.max_attempts,
        current_provider=provider_view,
        negotiations=negotiations,
        recommendation=recommendation,
        allowed_actions=allowed_actions,
        audit=[
            AuditEventView(type=e.event_type, payload=e.payload, at=e.created_at)
            for e in audit_rows
        ],
    )


def build_transactions_router(
    session_factory: Callable[[], Session],
    telephony: TelephonyProvider,
    settings: Settings,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/transactions", tags=["transactions"])

    def get_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    def get_tx(transaction_id: str, db: Session = Depends(get_db)) -> Transaction:
        tx = db.get(Transaction, transaction_id)
        if tx is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
        return tx

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=TransactionView)
    def create_transaction(
        body: TransactionCreate, db: Session = Depends(get_db)
    ) -> TransactionView:
        today = now().astimezone(ZoneInfo(settings.timezone)).date()
        requested = date.fromisoformat(body.service_date)  # schema already validated the format
        if not (today <= requested <= today + timedelta(days=90)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="service_date must be within the next 90 days",
            )

        tx = Transaction(
            request=body.request,
            service=body.service,
            service_date=body.service_date,
            location=body.location,
            max_budget=body.max_budget,
            max_attempts=body.max_attempts,
        )
        db.add(tx)
        db.flush()
        record_event(db, tx.id, "transaction.created", {"request": body.request})
        db.commit()
        return _build_view(db, tx)

    @router.get("/{transaction_id}", response_model=TransactionView)
    def get_transaction(
        tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        maybe_timeout_confirming(
            db, tx, timeout_seconds=settings.confirmation_stuck_timeout_seconds, now=now
        )
        return _build_view(db, tx)

    @router.post(
        "/{transaction_id}/start",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TransactionView,
        responses={409: {"model": ErrorDetail}, 429: {"model": ErrorDetail}},
    )
    async def start_transaction(
        tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        if tx.status != TxStatus.CREATED.value:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"transaction is {tx.status}, not CREATED"
            )
        # Check before select_provider: it commits CREATED -> PROVIDER_SELECTED, and a 429 after
        # that would strand the transaction with no allowed action to retry from.
        try:
            check_call_guard(db, tx, settings.max_calls_per_day, now)
        except CallGuardBlocked as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

        provider = select_provider(db, tx)
        if provider is not None:
            try:
                await place_call(
                    db,
                    tx,
                    provider,
                    telephony=telephony,
                    max_calls_per_day=settings.max_calls_per_day,
                    now=now,
                )
            except CallGuardBlocked as exc:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
            if tx.status == TxStatus.UNAVAILABLE.value:
                await advance_from_unavailable(
                    db,
                    tx,
                    telephony=telephony,
                    max_calls_per_day=settings.max_calls_per_day,
                    now=now,
                )
        return _build_view(db, tx)

    @router.post(
        "/{transaction_id}/approve",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TransactionView,
        responses={409: {"model": ErrorDetail}, 429: {"model": ErrorDetail}},
    )
    async def approve_transaction(
        body: ApproveBody, tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        if tx.status not in (TxStatus.RESULT_READY.value, TxStatus.AWAITING_APPROVAL.value):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"transaction is {tx.status}, not awaiting a decision",
            )
        latest_recommendation = db.scalar(
            select(Recommendation)
            .where(Recommendation.transaction_id == tx.id)
            .order_by(Recommendation.id.desc())
            .limit(1)
        )
        if latest_recommendation is None or latest_recommendation.offer_id != body.offer_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"offer_id {body.offer_id} does not match the current recommendation",
            )
        try:
            # Do this before recording the approval. A guard failure must leave the decision
            # available to the owner instead of stranding it at APPROVED.
            check_call_guard(db, tx, settings.max_calls_per_day, now)
            orchestrator_approve(db, tx, body.offer_id)
            await start_confirmation(
                db,
                tx,
                telephony=telephony,
                max_calls_per_day=settings.max_calls_per_day,
                now=now,
                guard_checked=True,
            )
        except StaleOffer as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except CallGuardBlocked as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        return _build_view(db, tx)

    @router.post(
        "/{transaction_id}/decline",
        response_model=TransactionView,
        responses={409: {"model": ErrorDetail}},
    )
    def decline_transaction(
        tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        # CONFIRMATION_FAILED is the owner abandoning a booking after a failed confirmation call,
        # rather than declining an offer before one was ever approved -- same DECLINED -> CLOSED
        # path either way (see orchestrator.decline's docstring).
        if tx.status not in (
            TxStatus.RESULT_READY.value,
            TxStatus.AWAITING_APPROVAL.value,
            TxStatus.CONFIRMATION_FAILED.value,
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"transaction is {tx.status}, not awaiting a decision",
            )
        orchestrator_decline(db, tx)
        return _build_view(db, tx)

    @router.post(
        "/{transaction_id}/retry-confirmation",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TransactionView,
        responses={409: {"model": ErrorDetail}, 429: {"model": ErrorDetail}},
    )
    async def retry_confirmation_transaction(
        tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        if tx.status not in (TxStatus.CONFIRMATION_FAILED.value, TxStatus.APPROVED.value):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"transaction is {tx.status}, not awaiting confirmation retry",
            )
        # APPROVED means the confirmation call never got as far as CONFIRMING (an exception
        # between recording approval and the dial), so there's no confirmation negotiation to
        # resolve -- start fresh, same as the happy path in /approve, instead of retrying a call
        # that never existed.
        negotiation = None
        if tx.status == TxStatus.CONFIRMATION_FAILED.value:
            negotiation = db.scalar(
                select(Negotiation)
                .where(
                    Negotiation.transaction_id == tx.id,
                    Negotiation.kind == "confirmation",
                )
                .order_by(Negotiation.created_at.desc(), Negotiation.id.desc())
                .limit(1)
            )
            if negotiation is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="transaction has no confirmation call to retry",
                )
        try:
            if negotiation is not None:
                await retry_confirmation(
                    db,
                    tx,
                    negotiation,
                    telephony=telephony,
                    max_calls_per_day=settings.max_calls_per_day,
                    now=now,
                )
            else:
                await start_confirmation(
                    db,
                    tx,
                    telephony=telephony,
                    max_calls_per_day=settings.max_calls_per_day,
                    now=now,
                )
        except CallGuardBlocked as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        except ApprovalRequired as exc:
            # Defensive only: APPROVED implies an APPROVED approvals row already exists.
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return _build_view(db, tx)

    return router
