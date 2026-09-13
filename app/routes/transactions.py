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
    CallGuardBlocked,
    StaleOffer,
    advance_from_unavailable,
    check_call_guard,
    place_call,
    select_provider,
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
    NegotiationView,
    OfferView,
    ProviderView,
    RecommendationView,
    TransactionCreate,
    TransactionView,
    TranscriptTurnView,
)
from app.states import TxStatus
from app.telephony.base import TelephonyProvider

log = logging.getLogger(__name__)

ALLOWED_ACTIONS_BY_STATUS: dict[str, tuple[str, ...]] = {
    TxStatus.CREATED.value: ("start",),
    TxStatus.RESULT_READY.value: ("approve", "decline"),
    TxStatus.AWAITING_APPROVAL.value: ("approve", "decline"),
    TxStatus.CONFIRMATION_FAILED.value: ("retry_confirmation",),
    TxStatus.CONFIRM_RETRY_WAIT.value: ("retry_confirmation",),
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

    allowed_actions = list(ALLOWED_ACTIONS_BY_STATUS.get(tx.status, ()))
    if recommendation is None or recommendation.offer_id is None:
        # An escalation reaches AWAITING_APPROVAL even when no price was ever quoted. With no
        # offer_id there is nothing approvable, so don't advertise an approve that can only 409.
        allowed_actions = [action for action in allowed_actions if action != "approve"]

    return TransactionView(
        id=tx.id,
        status=tx.status,
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
        return _build_view(db, tx)

    @router.post(
        "/{transaction_id}/start",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TransactionView,
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
    )
    def approve_transaction(
        body: ApproveBody, tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        if tx.status not in (TxStatus.RESULT_READY.value, TxStatus.AWAITING_APPROVAL.value):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"transaction is {tx.status}, not awaiting a decision",
            )
        try:
            orchestrator_approve(db, tx, body.offer_id)
        except StaleOffer as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        # The confirmation call (APPROVED -> CONFIRMING onward) is a stretch goal (#6 priority 4)
        # and isn't wired here: the demo scenario only needs to reach this approval/recommendation
        # state, not a confirmed booking. `app/orchestrator.py::start_confirmation` exists and is
        # unit-tested, ready to be called from here once that flow is finished.
        return _build_view(db, tx)

    @router.post("/{transaction_id}/decline", response_model=TransactionView)
    def decline_transaction(
        tx: Transaction = Depends(get_tx), db: Session = Depends(get_db)
    ) -> TransactionView:
        if tx.status not in (TxStatus.RESULT_READY.value, TxStatus.AWAITING_APPROVAL.value):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"transaction is {tx.status}, not awaiting a decision",
            )
        orchestrator_decline(db, tx)
        return _build_view(db, tx)

    return router
