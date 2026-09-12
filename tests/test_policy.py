from datetime import date

import pytest

from app.policy import (
    Decision,
    Evaluation,
    OfferTerms,
    PolicyStatus,
    ProviderOutcome,
    RecommendationKind,
    RecommendationResult,
    build_recommendation,
    counter_step,
    evaluate_offer,
    format_kes,
    next_counteroffer,
)

CAP = 20_000
MONDAY = date(2026, 9, 14)


@pytest.mark.parametrize(
    ("cap", "made", "max_attempts", "expected"),
    [
        (20_000, 0, 2, 19_500),
        (20_000, 1, 2, 20_000),
        (20_000, 2, 2, None),
        (20_000, 3, 2, None),
        (20_000, 0, 1, 20_000),
        (20_000, 1, 1, None),
        (20_000, 0, 0, None),
        (9_999, 0, 2, 9_700),
        (9_999, 1, 2, 9_999),
        (10_000, 0, 2, 9_500),
        (1_000, 0, 2, 900),
        (1_000_000, 0, 2, 975_000),
    ],
)
def test_next_counteroffer(cap, made, max_attempts, expected):
    assert next_counteroffer(cap, made, max_attempts) == expected


@pytest.mark.parametrize(("cap", "made", "max_attempts"), [(0, 0, 2), (CAP, -1, 2), (CAP, 0, -1)])
def test_invalid_limits_raise(cap, made, max_attempts):
    with pytest.raises(ValueError):
        next_counteroffer(cap, made, max_attempts)


def test_counter_step_switches_at_10000():
    assert (counter_step(9_999), counter_step(10_000)) == (100, 500)


def available(**terms) -> OfferTerms:
    return OfferTerms(available=True, **terms)


@pytest.mark.parametrize(
    ("offer", "limits", "expected"),
    [
        pytest.param(
            OfferTerms(available=False), {}, Evaluation(Decision.UNAVAILABLE), id="unavailable"
        ),
        pytest.param(
            OfferTerms(available=False, amount=18_000, deposit_required=True),
            {},
            Evaluation(Decision.UNAVAILABLE),
            id="unavailable-beats-deposit",
        ),
        pytest.param(
            available(amount=19_000, coverage_hours=6, deposit_required=True),
            {},
            Evaluation(Decision.MUST_ESCALATE, trigger="deposit_or_payment_request"),
            id="deposit-within-cap-escalates",
        ),
        pytest.param(
            available(), {}, Evaluation(Decision.CLARIFY, clarify_term="price"), id="no-price"
        ),
        pytest.param(
            available(),
            {"clarifications": 1},
            Evaluation(Decision.MUST_ESCALATE, trigger="incomplete_terms"),
            id="no-price-after-clarifying",
        ),
        pytest.param(
            available(amount=19_000),
            {},
            Evaluation(Decision.CLARIFY, clarify_term="coverage_hours"),
            id="within-cap-no-hours",
        ),
        pytest.param(
            available(amount=19_000),
            {"clarifications": 1},
            Evaluation(Decision.MUST_ESCALATE, trigger="incomplete_terms"),
            id="within-cap-no-hours-after-clarifying",
        ),
        pytest.param(
            available(amount=18_000, coverage_hours=8),
            {},
            Evaluation(Decision.MAY_ACCEPT),
            id="within-cap-no-counter-needed",
        ),
        pytest.param(
            available(amount=20_000, coverage_hours=6),
            {"made": 2},
            Evaluation(Decision.MAY_ACCEPT),
            id="at-cap-on-final-attempt-accepts",
        ),
        pytest.param(
            available(amount=23_000, coverage_hours=6),
            {},
            Evaluation(Decision.COUNTER, counter_amount=19_500),
            id="first-counter",
        ),
        pytest.param(
            available(amount=23_000),
            {},
            Evaluation(Decision.COUNTER, counter_amount=19_500),
            id="over-cap-counters-before-asking-hours",
        ),
        pytest.param(
            available(amount=21_000, coverage_hours=6),
            {"made": 1},
            Evaluation(Decision.COUNTER, counter_amount=20_000),
            id="final-counter-is-cap",
        ),
        pytest.param(
            available(amount=21_000, coverage_hours=6),
            {"made": 2},
            Evaluation(Decision.STOP_NEGOTIATING),
            id="attempts-exhausted",
        ),
        pytest.param(
            available(amount=23_000, coverage_hours=6, provider_refuses_negotiation=True),
            {},
            Evaluation(Decision.STOP_NEGOTIATING),
            id="provider-refuses-to-negotiate",
        ),
        pytest.param(
            available(amount=23_000, coverage_hours=6),
            {"max_attempts": 0},
            Evaluation(Decision.STOP_NEGOTIATING),
            id="no-negotiation-authority",
        ),
    ],
)
def test_evaluate_offer(offer, limits, expected):
    args = {"cap": CAP, "made": 0, "max_attempts": 2, "clarifications": 0} | limits
    assert evaluate_offer(offer, **args) == expected


def recommend(**overrides) -> RecommendationResult:
    args = {
        "cap": CAP,
        "service_date": MONDAY,
        "provider_name": "Provider One",
        "final_offer": None,
        "counteroffers_made": 0,
    }
    return build_recommendation(**(args | overrides))


def ask_user(reason: str) -> RecommendationResult:
    return RecommendationResult(PolicyStatus.REQUIRES_APPROVAL, RecommendationKind.ASK_USER, reason)


def test_rule1_no_provider_available_lists_each_outcome():
    result = recommend(
        provider_name=None,
        provider_outcomes=[
            ("Provider One", ProviderOutcome.NO_ANSWER),
            ("Provider Two", ProviderOutcome.NOT_AVAILABLE),
        ],
    )
    assert result == RecommendationResult(
        PolicyStatus.NONE,
        RecommendationKind.DECLINE,
        "No provider available on Mon 14 Sep: "
        "Provider One: no answer; Provider Two: not available.",
    )


def test_rule1_offer_marked_unavailable_declines():
    result = recommend(final_offer=OfferTerms(available=False))
    assert result.recommendation == RecommendationKind.DECLINE
    assert result.reason == "No provider available on Mon 14 Sep."


def test_rule2_deposit_request_asks_user_even_within_cap():
    result = recommend(
        final_offer=available(amount=19_000, coverage_hours=6, deposit_required=True),
        escalation_trigger="deposit_or_payment_request",
    )
    assert result == ask_user("Provider One asked for a deposit or payment. Offer: KES 19,000.")


def test_rule2_escalation_before_any_price():
    result = recommend(escalation_trigger="sensitive_information_request")
    assert result == ask_user("Provider One asked for sensitive information. No price was stated.")


def test_rule2_unknown_trigger_raises():
    with pytest.raises(ValueError, match="unknown escalation trigger"):
        recommend(escalation_trigger="made_up")


def test_rule3_matches_definition_of_done_text():
    result = recommend(final_offer=available(amount=21_000, coverage_hours=6), counteroffers_made=2)
    assert result == ask_user(
        "Final offer KES 21,000 is KES 1,000 (5.0%) over your KES 20,000 budget "
        "after 2 counteroffer(s)."
    )


def test_rule3_percentage_rounds_half_up():
    result = recommend(final_offer=available(amount=20_010, coverage_hours=6), counteroffers_made=1)
    assert result.reason == (
        "Final offer KES 20,010 is KES 10 (0.1%) over your KES 20,000 budget "
        "after 1 counteroffer(s)."
    )


def test_rule4_within_cap_without_hours_asks_user():
    result = recommend(final_offer=available(amount=19_000))
    assert result == ask_user("KES 19,000 is within budget, but coverage hours were not confirmed.")


@pytest.mark.parametrize(("hours", "text"), [(6.0, "6"), (6.5, "6.5"), (10, "10")])
def test_rule5_accept_formats_hours(hours, text):
    result = recommend(final_offer=available(amount=19_500, coverage_hours=hours))
    assert result == RecommendationResult(
        PolicyStatus.WITHIN_LIMIT,
        RecommendationKind.ACCEPT,
        f"KES 19,500 for {text} hours is within your KES 20,000 budget.",
    )


def test_dropped_call_appends_suffix():
    result = recommend(final_offer=available(amount=19_500, coverage_hours=6), call_dropped=True)
    assert result.reason == (
        "KES 19,500 for 6 hours is within your KES 20,000 budget. (call dropped before closing)"
    )


def test_format_kes_groups_thousands():
    assert format_kes(1_000_000) == "KES 1,000,000"
