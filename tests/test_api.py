"""Offline tests for the transaction + parse-request API (epic #10, contract 4).

FakeTelephonyProvider + ScriptedLLMProvider; no vendor SDK, no network, no real DB file.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit import record_event
from app.config import Settings
from app.db import init_db, make_engine
from app.llm.base import LLMError, LLMProvider, ToolCall
from app.llm.fake import ScriptedLLMProvider, reply, tool_use
from app.models import Approval, AuditEvent, Negotiation, Offer, Provider, Transaction
from app.orchestrator import on_call_answered, on_call_ended
from app.routes.parse import build_parse_router
from app.routes.transactions import build_transactions_router
from app.states import TERMINAL_STATES, TxStatus
from app.telephony.base import TelephonyError
from app.telephony.fake import FakeTelephonyProvider

NOW = lambda: datetime(2026, 9, 10, 9, 0, tzinfo=UTC)  # noqa: E731 - 2026-09-10 09:00 UTC = 12:00 Nairobi
CAP = 20000


class FailingTelephony(FakeTelephonyProvider):
    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self.fail_times = fail_times

    async def place_call(self, to_number: str):
        if self.fail_times:
            self.fail_times -= 1
            raise TelephonyError("simulated telephony failure")
        return await super().place_call(to_number)


VALID_TX_BODY = {
    "request": "Photographer for Monday in Nairobi. Maximum KES 20,000. Negotiate twice.",
    "service": "photography",
    "service_date": "2026-09-14",
    "location": "Nairobi",
    "max_budget": CAP,
    "max_attempts": 2,
}


def _settings(**overrides) -> Settings:
    values = dict(timezone="Africa/Nairobi", max_calls_per_day=40)
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    init_db(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _build_app(
    *,
    session_factory=None,
    telephony=None,
    llm: LLMProvider | None = None,
    settings: Settings | None = None,
    now=NOW,
):
    session_factory = session_factory or _session_factory()
    telephony = telephony or FakeTelephonyProvider()
    settings = settings or _settings()
    app = FastAPI()
    app.include_router(build_transactions_router(session_factory, telephony, settings, now=now))
    if llm is not None:
        app.include_router(build_parse_router(lambda: llm, settings, now=now))
    return app, session_factory, telephony


def _seed_provider(session_factory, **overrides) -> Provider:
    values = dict(
        name="Studio A", phone="+254100000678", location="Nairobi", priority=1, active=True
    )
    values.update(overrides)
    with session_factory() as db:
        provider = Provider(**values)
        db.add(provider)
        db.commit()
        db.refresh(provider)
        return provider


# --- POST /transactions -------------------------------------------------------------------------


def test_create_transaction_returns_201_and_a_created_view():
    app, _, _ = _build_app()
    client = TestClient(app)

    response = client.post("/transactions", json=VALID_TX_BODY)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "CREATED"
    assert body["allowed_actions"] == ["start"]
    assert body["current_provider"] is None
    assert body["negotiations"] == []
    assert body["recommendation"] is None


@pytest.mark.parametrize(
    "override",
    [
        {"max_budget": 500},  # below the 1000 floor
        {"max_budget": 2_000_000},  # above the 1,000,000 ceiling
        {"max_attempts": 3},  # outside 0-2
        {"request": ""},  # below min_length
        {"service_date": "not-a-date"},
    ],
)
def test_create_transaction_rejects_invalid_fields(override):
    app, _, _ = _build_app()
    client = TestClient(app)

    response = client.post("/transactions", json={**VALID_TX_BODY, **override})

    assert response.status_code == 422


def test_create_transaction_rejects_a_service_date_outside_the_90_day_window():
    app, _, _ = _build_app()
    client = TestClient(app)

    response = client.post("/transactions", json={**VALID_TX_BODY, "service_date": "2027-01-01"})

    assert response.status_code == 422


# --- GET /transactions/{id} ----------------------------------------------------------------------


def test_get_transaction_round_trips():
    app, _, _ = _build_app()
    client = TestClient(app)
    created = client.post("/transactions", json=VALID_TX_BODY).json()

    response = client.get(f"/transactions/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_unknown_transaction_is_404():
    app, _, _ = _build_app()
    client = TestClient(app)

    assert client.get("/transactions/does-not-exist").status_code == 404


# --- POST /transactions/{id}/start ----------------------------------------------------------------


def test_start_places_a_call_and_masks_the_provider_phone():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FakeTelephonyProvider()
    app, _, telephony = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    response = client.post(f"/transactions/{tx_id}/start")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "CALLING"
    assert body["current_provider"]["phone_masked"] == "+254 1•• ••• 678"
    assert telephony.placed_calls == ["+254100000678"]
    assert len(body["negotiations"]) == 1


def test_start_on_a_transaction_with_no_active_providers_reaches_failed():
    session_factory = _session_factory()
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    response = client.post(f"/transactions/{tx_id}/start")

    assert response.status_code == 202
    assert response.json()["status"] == "FAILED"


def test_start_twice_is_409():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")

    response = client.post(f"/transactions/{tx_id}/start")

    assert response.status_code == 409


def test_start_hitting_the_call_guard_is_429():
    session_factory = _session_factory()
    provider = _seed_provider(session_factory)
    app, _, telephony = _build_app(
        session_factory=session_factory, settings=_settings(max_calls_per_day=1)
    )
    client = TestClient(app)
    # An earlier transaction already used up today's one allowed call.
    earlier_tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        db.add(
            Negotiation(
                transaction_id=earlier_tx_id,
                provider_id=provider.id,
                kind="negotiation",
                created_at=NOW().isoformat(timespec="milliseconds"),
            )
        )
        db.commit()
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    response = client.post(f"/transactions/{tx_id}/start")

    assert response.status_code == 429
    assert "Daily call limit reached (1)" in response.json()["detail"]
    assert telephony.placed_calls == []


def test_start_blocked_by_the_call_guard_leaves_the_transaction_startable():
    """The guard runs before a provider is selected, so a 429 leaves the transaction CREATED with
    start still allowed, not stranded at PROVIDER_SELECTED (owner UI retry path, DESIGN.md)."""
    session_factory = _session_factory()
    provider = _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory, settings=_settings(max_calls_per_day=1))
    client = TestClient(app)
    earlier_tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        db.add(
            Negotiation(
                transaction_id=earlier_tx_id,
                provider_id=provider.id,
                kind="negotiation",
                created_at=NOW().isoformat(timespec="milliseconds"),
            )
        )
        db.commit()
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    assert client.post(f"/transactions/{tx_id}/start").status_code == 429

    body = client.get(f"/transactions/{tx_id}").json()
    assert body["status"] == "CREATED"
    assert body["allowed_actions"] == ["start"]
    assert body["current_provider"] is None


# --- POST /transactions/{id}/approve and /decline ------------------------------------------------


def _tx_at_result_ready(session_factory, tx_id: str) -> int:
    """Drive a started transaction straight to RESULT_READY with an ACCEPT recommendation,
    the way a real answered-and-agreed call would via app/orchestrator.py."""
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        negotiation = db.scalar(select(Negotiation).where(Negotiation.transaction_id == tx_id))
        on_call_answered(db, tx, negotiation, now=NOW)
        negotiation.status = "AGREED"
        negotiation.end_call_requested = True
        offer = Offer(
            negotiation_id=negotiation.id,
            amount=18000,
            available=True,
            coverage_hours=4,
            provider_words="18000 for 4 hours",
            source="provider_quote",
            decision="MAY_ACCEPT",
        )
        db.add(offer)
        db.flush()
        offer_id = offer.id
        db.commit()

    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        negotiation = db.scalar(select(Negotiation).where(Negotiation.transaction_id == tx_id))
        asyncio.run(
            on_call_ended(
                db,
                tx,
                negotiation,
                telephony=FakeTelephonyProvider(),
                max_calls_per_day=40,
                now=NOW,
            )
        )
    return offer_id


def test_approve_starts_a_confirmation_call():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, telephony = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "CONFIRMING"
    assert body["recommendation"]["offer_id"] == offer_id
    assert body["negotiations"][-1]["kind"] == "confirmation"
    assert telephony.placed_calls == ["+254100000678", "+254100000678"]


def test_approve_guard_block_leaves_the_decision_actionable():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory, settings=_settings(max_calls_per_day=1))
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    with session_factory() as db:
        negotiation = db.scalar(select(Negotiation).where(Negotiation.transaction_id == tx_id))
        negotiation.created_at = NOW().isoformat(timespec="milliseconds")
        db.commit()

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})

    assert response.status_code == 429
    view = client.get(f"/transactions/{tx_id}").json()
    assert view["status"] == "RESULT_READY"
    assert view["allowed_actions"] == ["approve", "decline"]
    with session_factory() as db:
        assert db.scalars(select(Approval).where(Approval.transaction_id == tx_id)).all() == []


def test_approve_returns_a_retryable_confirmation_failed_view_when_dialing_fails():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FailingTelephony(fail_times=0)
    app, _, _ = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    telephony.fail_times = 2

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})

    assert response.status_code == 202
    assert response.json()["status"] == "CONFIRMATION_FAILED"
    assert response.json()["allowed_actions"] == ["retry_confirmation", "decline"]


def test_approve_without_any_recommendation_is_409_and_dials_nothing():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, telephony = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        db.get(Transaction, tx_id).status = TxStatus.RESULT_READY.value
        db.commit()

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": 1})

    assert response.status_code == 409
    assert "does not match the current recommendation" in response.json()["detail"]
    assert telephony.placed_calls == []
    with session_factory() as db:
        assert db.scalars(select(Approval).where(Approval.transaction_id == tx_id)).all() == []


def test_approve_with_a_stale_offer_id_is_409_and_writes_no_approval_row():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id + 999})

    assert response.status_code == 409
    with session_factory() as db:
        assert db.scalars(select(Approval)).all() == []
        assert db.get(Transaction, tx_id).status == "RESULT_READY"  # unchanged


def test_approve_on_a_transaction_not_awaiting_a_decision_is_409():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]  # still CREATED

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": 1})

    assert response.status_code == 409


def test_a_recommendation_with_an_offer_allows_approve_and_decline():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    _tx_at_result_ready(session_factory, tx_id)

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status"] == "RESULT_READY"
    assert body["allowed_actions"] == ["approve", "decline"]


def test_an_escalation_before_any_price_does_not_allow_approve():
    """A deposit request before a price is quoted still lands in AWAITING_APPROVAL, but with no
    offer_id there is nothing to approve: the view offers decline only (owner UI, DESIGN.md)."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        negotiation = db.scalar(select(Negotiation).where(Negotiation.transaction_id == tx_id))
        on_call_answered(db, tx, negotiation, now=NOW)
        negotiation.status = "ESCALATED"
        negotiation.escalation_trigger = "deposit_or_payment_request"
        negotiation.end_call_requested = True
        db.commit()
        asyncio.run(
            on_call_ended(
                db,
                tx,
                negotiation,
                telephony=FakeTelephonyProvider(),
                max_calls_per_day=40,
                now=NOW,
            )
        )

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status"] == "AWAITING_APPROVAL"
    assert body["recommendation"]["offer_id"] is None
    assert body["allowed_actions"] == ["decline"]


def test_decline_closes_the_transaction():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    _tx_at_result_ready(session_factory, tx_id)

    response = client.post(f"/transactions/{tx_id}/decline")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CLOSED"
    assert body["allowed_actions"] == []


def test_decline_on_a_transaction_not_awaiting_a_decision_is_409():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    assert client.post(f"/transactions/{tx_id}/decline").status_code == 409


def test_decline_from_confirmation_failed_abandons_the_transaction():
    """The owner giving up on a booking after a failed confirmation call reuses the ordinary
    DECLINED -> CLOSED path (see TODOS.md "Recover from CONFIRMING / CONFIRMATION_FAILED...")."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        db.get(Transaction, tx_id).status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()

    response = client.post(f"/transactions/{tx_id}/decline")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CLOSED"
    assert body["terminal"] is True
    assert body["allowed_actions"] == []
    with session_factory() as db:
        decisions = [
            row.decision
            for row in db.scalars(
                select(Approval).where(Approval.transaction_id == tx_id).order_by(Approval.id)
            ).all()
        ]
        assert decisions == ["APPROVED", "DECLINED"]


# --- POST /transactions/{id}/retry-confirmation -----------------------------------------------


def test_retry_confirmation_rejects_a_transaction_that_is_not_confirmation_failed():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 409


def test_retry_confirmation_rejects_a_failed_transaction_without_a_confirmation_call():
    session_factory = _session_factory()
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        db.get(Transaction, tx_id).status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 409
    assert "no confirmation call" in response.json()["detail"]


def test_retry_confirmation_from_approved_starts_a_fresh_confirmation_call():
    """Recovers a transaction stranded at APPROVED by an exception between recording approval and
    the confirmation dial (see TODOS.md "Recover from CONFIRMING / CONFIRMATION_FAILED...").
    Simulated by forcing the status back to APPROVED with an APPROVED approvals row but no
    confirmation negotiation, the exact shape that failure leaves behind -- so this must start
    fresh via start_confirmation(), not resolve a negotiation that was never created."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FakeTelephonyProvider()
    app, _, _ = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        tx.status = TxStatus.APPROVED.value
        db.add(Approval(transaction_id=tx_id, offer_id=offer_id, decision="APPROVED"))
        db.commit()
        assert db.scalars(select(Negotiation).where(Negotiation.kind == "confirmation")).all() == []

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "CONFIRMING"
    assert body["negotiations"][-1]["kind"] == "confirmation"
    # One dial from /start's negotiation call, one from this confirmation retry.
    assert telephony.placed_calls == ["+254100000678", "+254100000678"]


def test_retry_confirmation_retries_the_latest_confirmation_call():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FakeTelephonyProvider()
    app, _, _ = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        tx.status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 202
    assert response.json()["status"] == "CONFIRMING"
    assert response.json()["negotiations"][-1]["kind"] == "confirmation"
    assert len(telephony.placed_calls) == 3


def test_retry_confirmation_is_blocked_by_the_daily_call_guard():
    """The retry button dials a real provider like any other call; it must not be a way to bypass
    MAX_CALLS_PER_DAY just because the confirmation negotiation row already exists."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FakeTelephonyProvider()
    app, _, _ = _build_app(
        session_factory=session_factory,
        telephony=telephony,
        settings=_settings(max_calls_per_day=2),
    )
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        negotiations = db.scalars(select(Negotiation).where(Negotiation.transaction_id == tx_id))
        for negotiation in negotiations:
            negotiation.created_at = NOW().isoformat(timespec="milliseconds")
        db.get(Transaction, tx_id).status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()
    calls_before = len(telephony.placed_calls)

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 429
    assert len(telephony.placed_calls) == calls_before  # no new dial
    view = client.get(f"/transactions/{tx_id}").json()
    assert view["status"] == "CONFIRMATION_FAILED"
    assert view["allowed_actions"] == ["retry_confirmation", "decline"]


def test_retry_confirmation_dials_a_fresh_negotiation_not_the_failed_one():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FakeTelephonyProvider()
    app, _, _ = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        failed = db.scalar(
            select(Negotiation).where(
                Negotiation.transaction_id == tx_id, Negotiation.kind == "confirmation"
            )
        )
        failed_id = failed.id
        db.get(Transaction, tx_id).status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 202
    negotiations = response.json()["negotiations"]
    assert negotiations[-1]["kind"] == "confirmation"
    assert negotiations[-1]["id"] != failed_id


def test_retry_confirmation_that_fails_again_stays_retryable():
    """A retry that can't get through must land back on CONFIRMATION_FAILED with the retry still
    advertised, rather than stranding the owner in CONFIRMING with nothing to press."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    telephony = FailingTelephony(fail_times=0)
    app, _, _ = _build_app(session_factory=session_factory, telephony=telephony)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        db.get(Transaction, tx_id).status = TxStatus.CONFIRMATION_FAILED.value
        db.commit()
    telephony.fail_times = 2

    response = client.post(f"/transactions/{tx_id}/retry-confirmation")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "CONFIRMATION_FAILED"
    assert body["allowed_actions"] == ["retry_confirmation", "decline"]


def test_retry_confirmation_unknown_transaction_is_404():
    app, _, _ = _build_app()

    assert TestClient(app).post("/transactions/stale-id/retry-confirmation").status_code == 404


def test_confirmation_retry_is_advertised_only_after_a_failed_confirmation_call():
    session_factory = _session_factory()
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        tx.status = TxStatus.CONFIRMING.value
        db.commit()

    # CONFIRMING is a real in-flight state that also advertises no actions -- the owner can only
    # wait for the callback that resolves it (or the timeout below), same as CONFIRMATION_FAILED
    # before a retry. No confirmation negotiation exists in this test, so maybe_timeout_confirming
    # is a no-op regardless of elapsed time.
    assert client.get(f"/transactions/{tx_id}").json()["allowed_actions"] == []


def test_a_stuck_confirming_call_times_out_on_poll():
    """CONFIRMING has no route and no allowed_actions of its own: the only way out when a Twilio
    callback never arrives (a dead tunnel) is a poll noticing the confirmation call has been
    running too long. See TODOS.md "Recover from CONFIRMING / CONFIRMATION_FAILED...".
    """
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(
        session_factory=session_factory, settings=_settings(confirmation_stuck_timeout_seconds=60)
    )
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        negotiation = db.scalar(
            select(Negotiation).where(
                Negotiation.transaction_id == tx_id, Negotiation.kind == "confirmation"
            )
        )
        negotiation.created_at = (NOW() - timedelta(seconds=61)).isoformat(timespec="milliseconds")
        db.commit()

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status"] == "CONFIRMATION_FAILED"
    assert body["allowed_actions"] == ["retry_confirmation", "decline"]
    with session_factory() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.transaction_id == tx_id, AuditEvent.event_type == "call.timeout"
            )
        ).all()
        assert len(events) == 1


def test_a_confirming_call_within_the_timeout_window_stays_confirming():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(
        session_factory=session_factory, settings=_settings(confirmation_stuck_timeout_seconds=60)
    )
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)
    client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})
    with session_factory() as db:
        negotiation = db.scalar(
            select(Negotiation).where(
                Negotiation.transaction_id == tx_id, Negotiation.kind == "confirmation"
            )
        )
        negotiation.created_at = (NOW() - timedelta(seconds=30)).isoformat(timespec="milliseconds")
        db.commit()

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status"] == "CONFIRMING"
    assert body["allowed_actions"] == []


# --- audit trail visible on the view --------------------------------------------------------------


def test_a_malformed_status_changed_row_degrades_the_history_instead_of_500ing():
    """A status.changed audit row missing "from"/"to" (a hand-edited row, a future schema change,
    a test-only route) must not KeyError inside the one route the owner has to see a transaction
    at all."""
    session_factory = _session_factory()
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        tx = db.get(Transaction, tx_id)
        record_event(db, tx.id, "status.changed", {"reason": "hand-edited, no from/to"})
        db.commit()

    response = client.get(f"/transactions/{tx_id}")

    assert response.status_code == 200
    assert response.json()["status_history"] == ["CREATED"]


def test_view_includes_the_audit_trail_newest_first():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["audit"][0]["type"] == "transaction.created"


# --- status contract for the owner UI ------------------------------------------------------------
#
# The UI must never re-derive the state machine. The view carries the status vocabulary itself:
#   status          one TxStatus value (published as an enum in /openapi.json)
#   terminal        status ∈ TERMINAL_STATES, so the poll loop knows when to stop
#   status_history  every status the transaction has actually been in, oldest first, built from
#                   status.changed rows and NOT from the 50-event audit list


def test_a_new_transaction_has_a_one_entry_history_and_is_not_terminal():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status_history"] == ["CREATED"]
    assert body["terminal"] is False


def test_status_history_follows_every_transition_in_order():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    _tx_at_result_ready(session_factory, tx_id)

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status_history"] == [
        "CREATED",
        "PROVIDER_SELECTED",
        "CALLING",
        "NEGOTIATING",
        "AGREED_WITHIN_POLICY",
        "RESULT_READY",
    ]
    assert body["status_history"][-1] == body["status"]


def test_status_history_is_not_truncated_by_the_audit_cap():
    """A real call writes dozens of non-status events (call.input_received, tool.called, ...).
    The audit list is capped at 50; the history must still reach back to CREATED."""
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    with session_factory() as db:
        for turn in range(60):
            record_event(db, tx_id, "call.input_received", {"text": f"turn {turn}"})
        db.commit()

    body = client.get(f"/transactions/{tx_id}").json()

    assert len(body["audit"]) == 50
    assert all(event["type"] != "status.changed" for event in body["audit"])
    assert body["status_history"][:3] == ["CREATED", "PROVIDER_SELECTED", "CALLING"]


def test_a_declined_transaction_is_terminal_and_never_passed_through_confirming():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    _tx_at_result_ready(session_factory, tx_id)

    body = client.post(f"/transactions/{tx_id}/decline").json()

    assert body["terminal"] is True
    assert body["status_history"][-2:] == ["DECLINED", "CLOSED"]
    assert "CONFIRMING" not in body["status_history"]


@pytest.mark.parametrize("status", list(TxStatus))
def test_terminal_is_true_exactly_for_the_terminal_states(status):
    session_factory = _session_factory()
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    with session_factory() as db:
        db.get(Transaction, tx_id).status = status.value  # direct write: this tests the view only
        db.commit()

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["status"] == status.value
    assert body["terminal"] is (status in TERMINAL_STATES)


def test_openapi_publishes_the_status_enum_for_status_and_status_history():
    app, _, _ = _build_app()
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    view = schemas["TransactionView"]["properties"]

    def enum_of(prop: dict) -> set[str]:
        ref = prop["$ref"].rsplit("/", 1)[-1]
        return set(schemas[ref]["enum"])

    every_status = {s.value for s in TxStatus}
    assert enum_of(view["status"]) == every_status
    assert enum_of(view["status_history"]["items"]) == every_status
    assert view["terminal"]["type"] == "boolean"


# --- POST /parse-request -------------------------------------------------------------------------


def _parsed_call(**overrides) -> ToolCall:
    args = dict(
        service="photography",
        service_date="2026-09-14",
        date_text="Monday",
        location="Nairobi",
        max_budget=20000,
        max_attempts=2,
    )
    args.update(overrides)
    return ToolCall("t1", "submit_parsed_request", args)


def test_parse_request_returns_the_definition_of_done_scenario():
    llm = ScriptedLLMProvider([tool_use(_parsed_call())])
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post(
        "/parse-request",
        json={
            "text": "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; "
            "never agree above KES 20,000 without my approval."
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_date"] == "2026-09-14"
    assert body["max_budget"] == 20000
    assert body["max_attempts"] == 2
    assert body["missing"] == []


def test_parse_request_reports_missing_fields():
    llm = ScriptedLLMProvider(
        [tool_use(_parsed_call(service_date=None, location=None, max_budget=None))]
    )
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "I need a photographer sometime"})

    assert response.status_code == 200
    assert set(response.json()["missing"]) == {"service_date", "location", "max_budget"}


def test_parse_request_clamps_max_attempts():
    llm = ScriptedLLMProvider([tool_use(_parsed_call(max_attempts=7))])
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "whatever"})

    assert response.json()["max_attempts"] == 2


def test_parse_request_rejects_a_service_date_out_of_range():
    llm = ScriptedLLMProvider([tool_use(_parsed_call(service_date="2030-01-01"))])
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "whatever"})

    body = response.json()
    assert body["service_date"] is None
    assert "service_date" in body["missing"]


def test_parse_request_llm_error_is_502():
    llm = ScriptedLLMProvider([LLMError("boom", retryable=False)])
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "whatever"})

    assert response.status_code == 502


def test_parse_request_without_a_tool_call_is_502():
    llm = ScriptedLLMProvider([reply("I have a question first")])
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "whatever"})

    assert response.status_code == 502


def test_parse_request_body_too_long_is_422():
    app, _, _ = _build_app(llm=ScriptedLLMProvider([]))
    client = TestClient(app)

    response = client.post("/parse-request", json={"text": "x" * 501})

    assert response.status_code == 422


@pytest.mark.live
async def test_parse_request_definition_of_done_against_real_anthropic():
    from app.config import get_settings
    from app.llm.anthropic import AnthropicLLMProvider

    settings = get_settings()
    llm = AnthropicLLMProvider(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    app, _, _ = _build_app(llm=llm)
    client = TestClient(app)

    response = client.post(
        "/parse-request",
        json={
            "text": "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; "
            "never agree above KES 20,000 without my approval."
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_date"] == "2026-09-14"
    assert body["max_budget"] == 20000
    assert body["max_attempts"] == 2
    assert body["missing"] == []
