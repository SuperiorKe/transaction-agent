import pytest
from sqlalchemy import select

from app.models import AuditEvent, Transaction
from app.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransition,
    TxStatus,
    can_transition,
    transition,
)

T = TxStatus

ILLEGAL = [
    (T.CREATED, T.APPROVED),
    (T.CREATED, T.CONFIRMED),
    (T.CALLING, T.RESULT_READY),
    (T.NEGOTIATING, T.APPROVED),
    (T.RESULT_READY, T.CONFIRMING),
    (T.AWAITING_APPROVAL, T.CONFIRMED),
    (T.APPROVED, T.CONFIRMED),
    (T.CONFIRMED, T.CONFIRMING),
    (T.CLOSED, T.CREATED),
    (T.FAILED, T.PROVIDER_SELECTED),
]


def make_tx(session, status: TxStatus) -> Transaction:
    tx = Transaction(
        request="Photographer for Monday in Nairobi. Maximum KES 20,000.",
        service_date="2026-09-14",
        location="Nairobi",
        max_budget=20000,
        max_attempts=2,
        status=status.value,
    )
    session.add(tx)
    session.flush()
    return tx


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED_TRANSITIONS))
def test_allowed_transition_updates_status_and_audits(session, current, target):
    tx = make_tx(session, current)

    transition(session, tx, target, reason="test")
    session.flush()

    assert tx.status == target
    event = session.scalars(select(AuditEvent).where(AuditEvent.transaction_id == tx.id)).one()
    assert event.event_type == "status.changed"
    assert event.payload == {"from": current.value, "to": target.value, "reason": "test"}


@pytest.mark.parametrize(("current", "target"), ILLEGAL)
def test_illegal_transition_raises_and_leaves_state_untouched(session, current, target):
    assert not can_transition(current, target)
    tx = make_tx(session, current)

    with pytest.raises(IllegalTransition):
        transition(session, tx, target, reason="test")

    assert tx.status == current
    assert session.scalars(select(AuditEvent)).all() == []


def test_terminal_states_have_no_outgoing_transitions():
    assert [pair for pair in ALLOWED_TRANSITIONS if pair[0] in TERMINAL_STATES] == []


def test_every_non_terminal_state_has_a_way_out():
    sources = {current for current, _ in ALLOWED_TRANSITIONS}
    assert set(TxStatus) - TERMINAL_STATES <= sources
