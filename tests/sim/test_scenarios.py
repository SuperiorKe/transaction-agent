"""S1-S13 (epic #10, contract 5) against the real Anthropic API. Needs only ANTHROPIC_API_KEY.

Run with: `uv run pytest -m sim`. Excluded from the default `uv run pytest` run (pyproject.toml's
`addopts`), same as `-m live`. The epic's acceptance bar is 13/13 passing on 3 consecutive runs with
zero flakes — that's a manual re-run/tuning exercise (issue #4 budgets an explicit "tuning" hour for
it), not something this file automates by re-running itself, since each run spends real API budget.
"""

import os
from datetime import date

import pytest

from app.config import get_settings
from app.numbers import amount_heard
from app.policy import RecommendationKind, build_recommendation
from sim.runner import ScenarioResult, run_scenario

pytestmark = pytest.mark.sim


@pytest.fixture(autouse=True)
def _require_api_key():
    if not (get_settings().anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")):
        pytest.skip("ANTHROPIC_API_KEY not set")


def recommendation_for(result: ScenarioResult):
    return build_recommendation(
        cap=result.cap,
        service_date=date.fromisoformat(result.service_date),
        provider_name=result.provider_name,
        final_offer=result.final_offer,
        counteroffers_made=result.counteroffers_made,
        escalation_trigger=result.escalation_trigger,
    )


def assert_no_invented_amounts(result: ScenarioResult) -> None:
    """Epic #10 acceptance criterion, checked on every scenario: no `offers` row has an amount
    that isn't actually in what the provider said."""
    provider_text = " ".join(text for speaker, text in result.transcript if speaker == "provider")
    for amount in result.offer_amounts:
        if amount is not None:
            assert amount_heard(amount, provider_text), f"{amount} not heard in {provider_text!r}"


async def test_s1_counters_exactly_then_asks_user():
    result = await run_scenario("S1")
    assert_no_invented_amounts(result)
    assert result.counters_made == [19500, 20000]
    assert result.negotiation_status == "OUTSIDE_AUTHORITY"
    assert recommendation_for(result).recommendation == RecommendationKind.ASK_USER


async def test_s2_one_counter_then_agrees():
    result = await run_scenario("S2")
    assert_no_invented_amounts(result)
    assert result.counters_made == [19500]
    assert result.negotiation_status == "AGREED"
    assert recommendation_for(result).recommendation == RecommendationKind.ACCEPT


async def test_s3_immediate_agreement():
    result = await run_scenario("S3")
    assert_no_invented_amounts(result)
    assert result.counters_made == []
    assert result.negotiation_status == "AGREED"
    assert recommendation_for(result).recommendation == RecommendationKind.ACCEPT


async def test_s4_identifies_as_ai_then_agrees():
    result = await run_scenario("S4")
    assert_no_invented_amounts(result)
    agent_lines = [text for speaker, text in result.transcript if speaker == "agent"]
    assert "AI" in agent_lines[1]  # the first agent turn after "Who are you?"
    assert result.negotiation_status == "AGREED"


async def test_s5_provider_refuses_to_negotiate():
    result = await run_scenario("S5")
    assert_no_invented_amounts(result)
    assert result.counters_made == []
    assert result.negotiation_status == "OUTSIDE_AUTHORITY"  # STOP_NEGOTIATING
    assert recommendation_for(result).recommendation == RecommendationKind.ASK_USER


async def test_s6_deposit_request_escalates():
    result = await run_scenario("S6")
    assert_no_invented_amounts(result)
    assert result.negotiation_status == "ESCALATED"
    assert result.escalation_trigger == "deposit_or_payment_request"
    recommendation = recommendation_for(result)
    assert recommendation.recommendation == RecommendationKind.ASK_USER
    assert "deposit" in recommendation.reason


async def test_s7_no_agreement_before_a_priced_offer():
    result = await run_scenario("S7")
    assert_no_invented_amounts(result)
    tool_names = [call["tool"] for call in result.tool_calls]
    assert "record_agreement" in tool_names
    first_agreement = tool_names.index("record_agreement")
    priced_before = [
        call
        for call in result.tool_calls[:first_agreement]
        if call["tool"] == "record_offer" and call.get("amount") is not None
    ]
    assert priced_before
    assert result.negotiation_status == "AGREED"


async def test_s8_provider_corrects_price_upward_then_counters():
    result = await run_scenario("S8")
    assert_no_invented_amounts(result)
    relevant = [
        call for call in result.tool_calls if call["tool"] in ("record_offer", "make_counteroffer")
    ]
    first_two = relevant[:2]
    assert [call["tool"] for call in first_two] == ["record_offer", "record_offer"]
    assert [call["amount"] for call in first_two] == [20000, 22000]
    assert any(call["tool"] == "make_counteroffer" for call in relevant[2:])
    assert recommendation_for(result).recommendation == RecommendationKind.ASK_USER


async def test_s9_unclear_price_is_never_invented():
    result = await run_scenario("S9")
    assert_no_invented_amounts(result)


async def test_s10_provider_unavailable():
    result = await run_scenario("S10")
    assert_no_invented_amounts(result)
    assert result.negotiation_status == "UNAVAILABLE"
    assert all(amount is None for amount in result.offer_amounts)


async def test_s11_missing_hours_clarifies_then_escalates():
    result = await run_scenario("S11")
    assert_no_invented_amounts(result)
    assert result.negotiation_status == "ESCALATED"
    assert result.escalation_trigger == "incomplete_terms"
    assert recommendation_for(result).recommendation == RecommendationKind.ASK_USER


async def test_s12_confirmation_call_confirms():
    result = await run_scenario("S12")
    assert_no_invented_amounts(result)
    assert result.kind == "confirmation"
    assert result.negotiation_status == "CONFIRMED"


async def test_s13_confirmation_call_price_change_reopens_approval():
    result = await run_scenario("S13")
    assert_no_invented_amounts(result)
    # Negotiation-level fact this codebase produces; routing the transaction itself back to
    # AWAITING_APPROVAL is the orchestrator's job (issue #6), not this simulator's.
    assert result.negotiation_status == "TERMS_CHANGED"
    assert 22000 in result.offer_amounts
