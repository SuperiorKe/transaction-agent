"""The application composition wires Twilio, the secret-path webhook, and negotiation lookup."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import init_db, make_engine
from app.llm.base import LLMProvider
from app.llm.fake import ScriptedLLMProvider, reply
from app.main import create_app
from app.models import Negotiation, Provider, Transaction


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        twilio_account_sid="ACtest",
        twilio_auth_token="token",
        twilio_voice_number="+254200000000",
        webhook_base_url="https://tunnel.example",
        voice_webhook_secret="s3cret",
        **overrides,
    )


def _in_memory_session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    init_db(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_an_unknown_call_falls_back_to_the_preflight_stub():
    client = TestClient(create_app(_settings(), session_factory=_in_memory_session_factory()))

    response = client.post(
        "/webhooks/voice/s3cret",
        data={
            "CallSid": "CA123",
            "CallStatus": "in-progress",
            "Direction": "outbound-api",
            "From": "+254200000000",
            "To": "+254100000001",
        },
        headers={"cf-connecting-ip": "203.0.113.7"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Gather" in response.text
    assert "This is a brief phone connection test." in response.text


def test_twilio_webhook_refuses_an_incorrect_secret():
    client = TestClient(create_app(_settings()))

    response = client.post("/webhooks/voice/wrong", data={"CallSid": "CA123"})

    assert response.status_code == 404


def test_a_registered_negotiation_gets_the_real_negotiation_agent():
    """A call sid matching a negotiation's `provider_call_id` gets the real agent, not the stub."""
    session_factory = _in_memory_session_factory()
    with session_factory() as db:
        provider = Provider(
            name="Test Studio", phone="+254100000001", location="Nairobi", priority=1
        )
        db.add(provider)
        db.flush()
        tx = Transaction(
            request="Photographer for Monday. Max KES 20,000.",
            service_date="2026-09-14",
            location="Nairobi",
            max_budget=20000,
            max_attempts=2,
            current_provider_id=provider.id,
        )
        db.add(tx)
        db.flush()
        negotiation = Negotiation(
            transaction_id=tx.id,
            provider_id=provider.id,
            kind="negotiation",
            provider_call_id="CA999",
        )
        db.add(negotiation)
        db.commit()

    scripted = ScriptedLLMProvider([reply("Hello, are you available Monday for photography?")])

    def llm_factory(_settings: Settings) -> LLMProvider:
        return scripted

    client = TestClient(
        create_app(_settings(), session_factory=session_factory, llm_factory=llm_factory)
    )

    response = client.post(
        "/webhooks/voice/s3cret",
        data={
            "CallSid": "CA999",
            "CallStatus": "in-progress",
            "Direction": "outbound-api",
            "From": "+254200000000",
            "To": "+254100000001",
        },
        headers={"cf-connecting-ip": "203.0.113.7"},
    )

    assert response.status_code == 200
    assert "This is a brief phone connection test." not in response.text
    assert "are you available Monday" in response.text
    with session_factory() as db:
        started = db.scalar(select(Negotiation).where(Negotiation.provider_call_id == "CA999"))
        assert started is not None
