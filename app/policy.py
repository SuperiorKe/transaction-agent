"""Deterministic negotiation policy (epic #10, contract 3).

Pure functions: no I/O, no clock, no database. This is the code that stops an agreement above
the user's cap no matter what the voice model says; the model only ever sees its decisions.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class Decision(StrEnum):
    MAY_ACCEPT = "MAY_ACCEPT"
    COUNTER = "COUNTER"
    CLARIFY = "CLARIFY"
    MUST_ESCALATE = "MUST_ESCALATE"
    STOP_NEGOTIATING = "STOP_NEGOTIATING"
    UNAVAILABLE = "UNAVAILABLE"


class PolicyStatus(StrEnum):
    WITHIN_LIMIT = "WITHIN_LIMIT"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    NONE = "NONE"


class RecommendationKind(StrEnum):
    ACCEPT = "ACCEPT"
    ASK_USER = "ASK_USER"
    DECLINE = "DECLINE"


class ProviderOutcome(StrEnum):
    NO_ANSWER = "no answer"
    NOT_AVAILABLE = "not available"
    CALL_DROPPED = "call dropped"


# Escalation trigger -> what the provider did, as it reads after the provider's name.
TRIGGER_REASONS = {
    "deposit_or_payment_request": "asked for a deposit or payment",
    "cancellation_or_refund_terms": "asked for cancellation or refund terms",
    "sensitive_information_request": "asked for sensitive information",
    "unsupported_request": "asked for something outside my authority",
    "incomplete_terms": "didn't confirm all required terms (price and coverage hours)",
    "unclear_audio": "couldn't be heard clearly when stating the price",
}

DROPPED_SUFFIX = " (call dropped before closing)"


@dataclass(frozen=True)
class OfferTerms:
    available: bool
    amount: int | None = None  # whole KES
    coverage_hours: float | None = None
    deposit_required: bool | None = None
    provider_refuses_negotiation: bool = False


@dataclass(frozen=True)
class Evaluation:
    decision: Decision
    counter_amount: int | None = None
    clarify_term: str | None = None  # "price" | "coverage_hours"
    trigger: str | None = None


@dataclass(frozen=True)
class RecommendationResult:
    policy_status: PolicyStatus
    recommendation: RecommendationKind
    reason: str


def _check_limits(cap: int, made: int, max_attempts: int) -> None:
    if cap <= 0 or made < 0 or max_attempts < 0:
        raise ValueError(f"invalid limits: cap={cap} made={made} max_attempts={max_attempts}")


def counter_step(cap: int) -> int:
    return 500 if cap >= 10_000 else 100


def next_counteroffer(cap: int, made: int, max_attempts: int) -> int | None:
    """Amount of the next allowed counteroffer, or None when none are left.

    The last allowed counter is always the cap; earlier ones are cap x 0.975 rounded down to the
    step. Cap 20,000 with 2 attempts gives 19,500 then 20,000, matching doc 03 §5.
    """
    _check_limits(cap, made, max_attempts)
    if made >= max_attempts:
        return None
    if made == max_attempts - 1:
        return cap
    step = counter_step(cap)
    return (cap * 975 // 1000) // step * step


def _clarify_or_escalate(term: str, clarifications: int) -> Evaluation:
    if clarifications == 0:
        return Evaluation(Decision.CLARIFY, clarify_term=term)
    return Evaluation(Decision.MUST_ESCALATE, trigger="incomplete_terms")


def evaluate_offer(
    offer: OfferTerms, *, cap: int, made: int, max_attempts: int, clarifications: int
) -> Evaluation:
    """Decide what the agent may do next with the provider's latest offer.

    `made` is counteroffers already spoken. It only limits further counters: a price within the
    cap is acceptable even on the final attempt (doc 03 §6 read literally would forbid that).
    """
    _check_limits(cap, made, max_attempts)
    if not offer.available:
        return Evaluation(Decision.UNAVAILABLE)
    if offer.deposit_required:
        return Evaluation(Decision.MUST_ESCALATE, trigger="deposit_or_payment_request")
    if offer.amount is None:
        return _clarify_or_escalate("price", clarifications)
    if offer.amount <= cap:
        if offer.coverage_hours is None:
            return _clarify_or_escalate("coverage_hours", clarifications)
        return Evaluation(Decision.MAY_ACCEPT)
    if offer.provider_refuses_negotiation:
        return Evaluation(Decision.STOP_NEGOTIATING)
    counter = next_counteroffer(cap, made, max_attempts)
    if counter is None:
        return Evaluation(Decision.STOP_NEGOTIATING)
    return Evaluation(Decision.COUNTER, counter_amount=counter)


def format_kes(amount: int) -> str:
    return f"KES {amount:,}"


def format_day(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def format_hours(hours: float) -> str:
    return f"{hours:g}"


def percent_over(amount: int, cap: int) -> str:
    pct = (Decimal(amount - cap) * 100 / Decimal(cap)).quantize(Decimal("0.1"), ROUND_HALF_UP)
    return f"{pct}%"


def build_recommendation(
    *,
    cap: int,
    service_date: date,
    provider_name: str | None,
    final_offer: OfferTerms | None,
    counteroffers_made: int,
    escalation_trigger: str | None = None,
    call_dropped: bool = False,
    provider_outcomes: Sequence[tuple[str, ProviderOutcome]] = (),
) -> RecommendationResult:
    """Recommendation for the user after a call ends. First matching rule wins (contract 3)."""
    if escalation_trigger is not None and escalation_trigger not in TRIGGER_REASONS:
        raise ValueError(f"unknown escalation trigger: {escalation_trigger}")
    name = provider_name or "The provider"
    suffix = DROPPED_SUFFIX if call_dropped else ""

    # Rule 2 precedes rule 1 in code: an escalation can happen before any price is stated,
    # and that is a question for the user, not "no provider available".
    if escalation_trigger is not None:
        amount = final_offer.amount if final_offer else None
        offer_text = (
            f"Offer: {format_kes(amount)}." if amount is not None else "No price was stated."
        )
        reason = f"{name} {TRIGGER_REASONS[escalation_trigger]}. {offer_text}{suffix}"
        return RecommendationResult(
            PolicyStatus.REQUIRES_APPROVAL, RecommendationKind.ASK_USER, reason
        )

    # Rule 1: nothing usable came back from any provider.
    if final_offer is None or not final_offer.available or final_offer.amount is None:
        outcomes = "; ".join(f"{p}: {outcome}" for p, outcome in provider_outcomes)
        reason = f"No provider available on {format_day(service_date)}"
        reason += f": {outcomes}." if outcomes else "."
        return RecommendationResult(PolicyStatus.NONE, RecommendationKind.DECLINE, reason)

    amount = final_offer.amount
    # Rule 3
    if amount > cap:
        reason = (
            f"Final offer {format_kes(amount)} is {format_kes(amount - cap)} "
            f"({percent_over(amount, cap)}) over your {format_kes(cap)} budget "
            f"after {counteroffers_made} counteroffer(s).{suffix}"
        )
        return RecommendationResult(
            PolicyStatus.REQUIRES_APPROVAL, RecommendationKind.ASK_USER, reason
        )
    # Rule 4
    if final_offer.coverage_hours is None:
        reason = (
            f"{format_kes(amount)} is within budget, but coverage hours were not confirmed.{suffix}"
        )
        return RecommendationResult(
            PolicyStatus.REQUIRES_APPROVAL, RecommendationKind.ASK_USER, reason
        )
    # Rule 5
    reason = (
        f"{format_kes(amount)} for {format_hours(final_offer.coverage_hours)} hours "
        f"is within your {format_kes(cap)} budget.{suffix}"
    )
    return RecommendationResult(PolicyStatus.WITHIN_LIMIT, RecommendationKind.ACCEPT, reason)
