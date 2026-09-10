from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

# Closed set from epic #10, contract 1. Unknown types are a programming error, not data.
EVENT_TYPES = frozenset(
    {
        "transaction.created",
        "status.changed",
        "provider.selected",
        "call.created",
        "call.status",
        "call.answered",
        "call.ended",
        "call.redial",
        "call.guard_blocked",
        "stream.started",
        "stream.stopped",
        "tool.called",
        "tool.rejected",
        "policy.evaluated",
        "offer.recorded",
        "counteroffer.made",
        "escalation.raised",
        "recommendation.created",
        "approval.recorded",
        "confirmation.retry_scheduled",
        "time.nudge",
        "time.hard_end",
        "error",
    }
)


def record_event(
    session: Session,
    transaction_id: str | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to the session. The caller owns the commit."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown audit event type: {event_type}")
    event = AuditEvent(transaction_id=transaction_id, event_type=event_type, payload=payload or {})
    session.add(event)
    return event
