"""Transaction state machine (epic #10, contract 2).

Every status change goes through `transition()`. Anything not in ALLOWED_TRANSITIONS raises.
"""

from enum import StrEnum

from sqlalchemy.orm import Session

from app.audit import record_event
from app.models import Transaction


class TxStatus(StrEnum):
    CREATED = "CREATED"
    PROVIDER_SELECTED = "PROVIDER_SELECTED"
    CALLING = "CALLING"
    NEGOTIATING = "NEGOTIATING"
    AGREED_WITHIN_POLICY = "AGREED_WITHIN_POLICY"
    OUTSIDE_AUTHORITY = "OUTSIDE_AUTHORITY"
    UNAVAILABLE = "UNAVAILABLE"
    NEXT_PROVIDER = "NEXT_PROVIDER"
    RESULT_READY = "RESULT_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    CLOSED = "CLOSED"
    CONFIRMING = "CONFIRMING"
    CONFIRM_RETRY_WAIT = "CONFIRM_RETRY_WAIT"
    CONFIRMED = "CONFIRMED"
    CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
    FAILED = "FAILED"


class NegStatus(StrEnum):
    DIALING = "DIALING"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    AGREED = "AGREED"
    OUTSIDE_AUTHORITY = "OUTSIDE_AUTHORITY"
    ESCALATED = "ESCALATED"
    UNAVAILABLE = "UNAVAILABLE"
    NO_ANSWER = "NO_ANSWER"
    DROPPED = "DROPPED"
    CONFIRMED = "CONFIRMED"
    TERMS_CHANGED = "TERMS_CHANGED"
    DECLINED_BY_PROVIDER = "DECLINED_BY_PROVIDER"
    FAILED = "FAILED"


T = TxStatus

ALLOWED_TRANSITIONS: frozenset[tuple[TxStatus, TxStatus]] = frozenset(
    {
        (T.CREATED, T.PROVIDER_SELECTED),
        (T.CREATED, T.FAILED),
        (T.PROVIDER_SELECTED, T.CALLING),
        (T.PROVIDER_SELECTED, T.UNAVAILABLE),
        (T.CALLING, T.NEGOTIATING),
        (T.CALLING, T.UNAVAILABLE),
        (T.NEGOTIATING, T.AGREED_WITHIN_POLICY),
        (T.NEGOTIATING, T.OUTSIDE_AUTHORITY),
        (T.NEGOTIATING, T.UNAVAILABLE),
        (T.NEGOTIATING, T.CALLING),  # redial once after a drop with no offer
        (T.AGREED_WITHIN_POLICY, T.RESULT_READY),
        (T.OUTSIDE_AUTHORITY, T.AWAITING_APPROVAL),
        (T.UNAVAILABLE, T.NEXT_PROVIDER),
        (T.UNAVAILABLE, T.FAILED),
        (T.NEXT_PROVIDER, T.PROVIDER_SELECTED),
        (T.RESULT_READY, T.APPROVED),
        (T.AWAITING_APPROVAL, T.APPROVED),
        (T.RESULT_READY, T.DECLINED),
        (T.AWAITING_APPROVAL, T.DECLINED),
        (T.DECLINED, T.CLOSED),
        (T.APPROVED, T.CONFIRMING),
        (T.CONFIRMING, T.CONFIRMED),
        (T.CONFIRMING, T.CONFIRM_RETRY_WAIT),
        (T.CONFIRM_RETRY_WAIT, T.CONFIRMING),
        (T.CONFIRMING, T.CONFIRMATION_FAILED),
        (T.CONFIRMING, T.AGREED_WITHIN_POLICY),  # provider changed terms: re-approval
        (T.CONFIRMING, T.OUTSIDE_AUTHORITY),  # provider changed terms: re-approval
        (T.CONFIRMATION_FAILED, T.CONFIRMING),
    }
)

TERMINAL_STATES: frozenset[TxStatus] = frozenset({T.CONFIRMED, T.CLOSED, T.FAILED})


class IllegalTransition(Exception):
    pass


def can_transition(current: str, target: str) -> bool:
    return (TxStatus(current), TxStatus(target)) in ALLOWED_TRANSITIONS


def transition(session: Session, tx: Transaction, target: str, reason: str) -> None:
    """Move `tx` to `target` and audit it. The caller owns the commit."""
    current, target = TxStatus(tx.status), TxStatus(target)
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise IllegalTransition(f"{current} -> {target} is not allowed (tx {tx.id})")
    tx.status = target.value
    record_event(
        session,
        tx.id,
        "status.changed",
        {"from": current.value, "to": target.value, "reason": reason},
    )
