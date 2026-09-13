"""Offline tests for the transaction + parse-request API (epic #10, contract 4).

FakeTelephonyProvider + ScriptedLLMProvider; no vendor SDK, no network, no real DB file.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import init_db, make_engine
from app.llm.base import LLMError, LLMProvider, ToolCall
from app.llm.fake import ScriptedLLMProvider, reply, tool_use
from app.models import Approval, Negotiation, Offer, Provider, Transaction
from app.orchestrator import on_call_answered, on_call_ended
from app.routes.parse import build_parse_router
from app.routes.transactions import build_transactions_router
from app.telephony.fake import FakeTelephonyProvider

NOW = lambda: datetime(2026, 9, 10, 9, 0, tzinfo=UTC)  # noqa: E731 - 2026-09-10 09:00 UTC = 12:00 Nairobi
CAP = 20000

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
        name="Studio A", phone="+254712345678", location="Nairobi", priority=1, active=True
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
    assert body["current_provider"]["phone_masked"] == "+254 7•• ••• 678"
    assert telephony.placed_calls == ["+254712345678"]
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


def test_approve_transitions_to_approved():
    session_factory = _session_factory()
    _seed_provider(session_factory)
    app, _, _ = _build_app(session_factory=session_factory)
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]
    client.post(f"/transactions/{tx_id}/start")
    offer_id = _tx_at_result_ready(session_factory, tx_id)

    response = client.post(f"/transactions/{tx_id}/approve", json={"offer_id": offer_id})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["recommendation"]["offer_id"] == offer_id


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


# --- audit trail visible on the view --------------------------------------------------------------


def test_view_includes_the_audit_trail_newest_first():
    app, _, _ = _build_app()
    client = TestClient(app)
    tx_id = client.post("/transactions", json=VALID_TX_BODY).json()["id"]

    body = client.get(f"/transactions/{tx_id}").json()

    assert body["audit"][0]["type"] == "transaction.created"


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
