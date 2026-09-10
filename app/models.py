"""SQLAlchemy models. Mirrors the DDL in epic #10, contract 1."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (CheckConstraint("service = 'photography'", name="ck_providers_service"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    service: Mapped[str] = mapped_column(default="photography")
    phone: Mapped[str] = mapped_column(unique=True)  # E.164
    location: Mapped[str]
    priority: Mapped[int]  # 1 is called first
    active: Mapped[bool] = mapped_column(default=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("service = 'photography'", name="ck_transactions_service"),
        CheckConstraint("max_budget BETWEEN 1000 AND 1000000", name="ck_transactions_budget"),
        CheckConstraint("max_attempts BETWEEN 0 AND 2", name="ck_transactions_attempts"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    request: Mapped[str]
    service: Mapped[str] = mapped_column(default="photography")
    service_date: Mapped[str]  # ISO date, Africa/Nairobi
    location: Mapped[str]
    max_budget: Mapped[int]  # whole KES
    currency: Mapped[str] = mapped_column(default="KES")
    max_attempts: Mapped[int]
    status: Mapped[str] = mapped_column(default="CREATED")
    current_provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"))
    created_at: Mapped[str] = mapped_column(default=now_iso)
    updated_at: Mapped[str] = mapped_column(default=now_iso, onupdate=now_iso)


class Negotiation(Base):
    __tablename__ = "negotiations"
    __table_args__ = (
        CheckConstraint("kind IN ('negotiation','confirmation')", name="ck_negotiations_kind"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    kind: Mapped[str]
    dial_attempt: Mapped[int] = mapped_column(default=1)  # 2 = redial after a drop with no offer
    attempt_count: Mapped[int] = mapped_column(default=0)  # counteroffers spoken
    clarifications: Mapped[int] = mapped_column(default=0)
    price_rejections: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="DIALING")
    provider_call_id: Mapped[str | None] = mapped_column(unique=True)  # telephony session id
    end_call_requested: Mapped[bool] = mapped_column(default=False)
    escalation_trigger: Mapped[str | None]
    end_reason: Mapped[str | None]
    answered_at: Mapped[str | None]
    ended_at: Mapped[str | None]
    duration_seconds: Mapped[int | None]
    created_at: Mapped[str] = mapped_column(default=now_iso)


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"
    __table_args__ = (
        CheckConstraint(
            "speaker IN ('agent','provider','system')", name="ck_transcript_turns_speaker"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(ForeignKey("negotiations.id"))
    speaker: Mapped[str]
    text: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now_iso)


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint(
            "source IN ('provider_quote','provider_counter','confirmation_call')",
            name="ck_offers_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(ForeignKey("negotiations.id"))
    amount: Mapped[int | None]  # KES; NULL if not stated
    available: Mapped[bool]
    availability: Mapped[str | None]
    coverage_hours: Mapped[float | None]
    edited_photos: Mapped[int | None]
    delivery_days: Mapped[int | None]
    deposit_required: Mapped[bool | None]
    provider_refuses_negotiation: Mapped[bool] = mapped_column(default=False)
    terms: Mapped[str | None]
    provider_words: Mapped[str]
    source: Mapped[str]
    decision: Mapped[str]  # policy Decision at record time
    created_at: Mapped[str] = mapped_column(default=now_iso)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint(
            "policy_status IN ('WITHIN_LIMIT','REQUIRES_APPROVAL','NONE')",
            name="ck_recommendations_policy_status",
        ),
        CheckConstraint(
            "recommendation IN ('ACCEPT','ASK_USER','DECLINE')",
            name="ck_recommendations_recommendation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"))
    policy_status: Mapped[str]
    recommendation: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now_iso)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED','DECLINED')", name="ck_approvals_decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    decision: Mapped[str]
    approved_amount: Mapped[int | None]
    created_at: Mapped[str] = mapped_column(default=now_iso)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"))
    event_type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=now_iso)
