"""API request/response shapes (epic #10, contract 4). Pydantic only; no DB or business logic."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ServiceKind = Literal["photography", "unsupported"]
MissingField = Literal["service_date", "location", "max_budget"]
PolicyStatus = Literal["WITHIN_LIMIT", "REQUIRES_APPROVAL", "NONE"]
RecommendationKind = Literal["ACCEPT", "ASK_USER", "DECLINE"]
AllowedAction = Literal["start", "approve", "decline", "retry_confirmation"]


class ParseRequestBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ParsedRequest(BaseModel):
    service: ServiceKind
    service_date: str | None = None
    date_text: str | None = None
    location: str | None = None
    max_budget: int | None = None
    max_attempts: int = 2
    missing: list[MissingField] = Field(default_factory=list)


class TransactionCreate(BaseModel):
    request: str = Field(min_length=1, max_length=2000)
    service: Literal["photography"]
    service_date: str  # ISO date; [today, today+90] in Africa/Nairobi is enforced by the route
    location: str = Field(min_length=1, max_length=200)
    max_budget: int = Field(ge=1000, le=1_000_000)
    max_attempts: Literal[0, 1, 2]

    @field_validator("service_date")
    @classmethod
    def _parses_as_iso_date(cls, value: str) -> str:
        from datetime import date

        date.fromisoformat(value)  # raises ValueError -> 422
        return value


class ApproveBody(BaseModel):
    offer_id: int


class ProviderView(BaseModel):
    id: int
    name: str
    phone_masked: str


class TranscriptTurnView(BaseModel):
    speaker: str
    text: str
    at: str


class OfferView(BaseModel):
    id: int
    amount: int | None
    available: bool
    coverage_hours: float | None
    deposit_required: bool | None
    terms: str | None
    decision: str
    at: str


class NegotiationView(BaseModel):
    id: str
    kind: str
    provider_name: str
    status: str
    attempt_count: int
    dial_attempt: int
    answered_at: str | None
    duration_seconds: int | None
    transcript: list[TranscriptTurnView]
    offers: list[OfferView]


class RecommendationView(BaseModel):
    offer_id: int | None
    provider_name: str | None
    final_price: int | None
    policy_status: PolicyStatus
    recommendation: RecommendationKind
    reason: str


class AuditEventView(BaseModel):
    type: str
    payload: dict
    at: str


class TransactionView(BaseModel):
    id: str
    status: str
    request: str
    service: Literal["photography"]
    service_date: str
    location: str
    max_budget: int
    currency: Literal["KES"]
    max_attempts: int
    current_provider: ProviderView | None
    negotiations: list[NegotiationView]
    recommendation: RecommendationView | None
    allowed_actions: list[AllowedAction]
    audit: list[AuditEventView]
