"""Concrete negotiation and confirmation tools (epic #10, contract 5).

`NegotiationToolExecutor` is the `ToolExecutor` (see `app/agent/engine.py`) that turns the model's
tool calls into database writes, gated by `app/policy.py` and `app/states.py`. It never decides an
outcome itself — `evaluate_offer`/`next_counteroffer` do that; this module only records what
happened and translates the result into a tool result the model can read.

Three conventions not spelled out verbatim in contract 5, chosen for consistency:
- `say` is present only when there is a scripted line to speak. Contract 5's negotiation prompt
  says "when a tool result includes a `say` value, speak that text verbatim" — implying its absence
  (e.g. on a rejected tool call) means the model composes its own words under the hard rules,
  instead of being handed a canned sentence for a purely internal, non-conversational failure
  (wrong counter amount, agreement on a stale offer, and so on).
- `is_error=True` is used exactly for calls contract 5 calls "rejections" (and that write
  `tool.rejected`): an unheard price, a wrong/late counteroffer, or an agreement on a non-latest or
  non-MAY_ACCEPT offer. A legitimate negotiation outcome that ends the call with bad news for the
  client (`UNAVAILABLE`, `MUST_ESCALATE`, `STOP_NEGOTIATING`) is not a rejection — the tool call
  itself succeeded, so `is_error=False`.
- Only `end_call` ever returns `ToolOutcome(end_call=True)` — contract 5 says so explicitly only
  for that tool. `record_offer`, `record_agreement`, and `escalate` record a fact or a decision and
  return `say` text, but never hang up by themselves: the model must call `end_call` once it's
  actually done talking. This matters in practice — e.g. a provider who states an in-cap price and
  then immediately corrects it (scenario S8) needs the call still open on the next turn even after
  a `MAY_ACCEPT`/`record_agreement`. `record_offer` also resets `negotiation.status` back to
  `IN_PROGRESS` at the top of every call, so a stale `AGREED` from an earlier, superseded offer
  never lingers once the provider says something new.

The confirmation-call prompt content and `record_confirmation`'s exact branching are authored here,
not quoted from contract 5 — the epic gives the confirmation tools' signatures and two example
scenarios (S12, S13) but not verbatim say text or internal decision rules the way it does for the
negotiation call. Flagged inline below.
"""

import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import ToolOutcome
from app.audit import record_event
from app.llm.base import ToolCall, ToolSpec
from app.models import Approval, Negotiation, Offer, Provider, Transaction, TranscriptTurn
from app.numbers import amount_heard
from app.policy import (
    TRIGGER_REASONS,
    Decision,
    OfferTerms,
    evaluate_offer,
    format_hours,
    format_kes,
    next_counteroffer,
)
from app.states import NegStatus

REQUIRED_TERMS = ("availability", "price", "coverage_hours")  # D13

SAY_UNAVAILABLE = "Thanks for letting me know. Goodbye."
SAY_MUST_ESCALATE = "I can't agree to that on this call. I'll pass it to my client. Goodbye."
SAY_CLARIFY_PRICE = "What's your price for that day?"
SAY_CLARIFY_HOURS = "And how many hours of coverage does that include?"


def _speak_date(day: date) -> str:
    return f"{day:%A}, {day.day} {day:%B}"


def _say_stop_negotiating(cap: int, amount: int) -> str:
    cap_text = format_kes(cap)
    return (
        f"Thanks. I'm not authorized to agree above {cap_text}. I'll take your offer of "
        f"{format_kes(amount)} to my client, and I won't confirm without their approval. Goodbye."
    )


def _say_may_accept(amount: int, hours: float) -> str:
    return (
        f"Great, {format_kes(amount)} for {format_hours(hours)} hours works within my client's "
        "budget. My client will review it and I'll call back to confirm. Nothing is booked until "
        "then. Goodbye."
    )


def _query_latest_offer(session: Session, negotiation_id: str) -> Offer | None:
    return session.scalar(
        select(Offer)
        .where(Offer.negotiation_id == negotiation_id)
        .order_by(Offer.id.desc())
        .limit(1)
    )


def closing_say(session: Session, tx: Transaction, negotiation: Negotiation) -> str:
    """The line to speak for `negotiation`'s current DB state, independent of the model.

    Shared by the `end_call` tool ("the outcome comes from DB state, never from reason") and
    `NegotiationCall`'s hard-end circuit breaker (`app/agent/session.py`), which "skips the model
    and speaks the closing line for the current DB state" — contract 5, same mechanism both places.
    """
    status = negotiation.status
    latest = _query_latest_offer(session, negotiation.id)
    if status == NegStatus.UNAVAILABLE.value:
        return SAY_UNAVAILABLE
    if status == NegStatus.ESCALATED.value:
        return SAY_MUST_ESCALATE
    if status == NegStatus.OUTSIDE_AUTHORITY.value and latest is not None:
        assert latest.amount is not None
        return _say_stop_negotiating(tx.max_budget, latest.amount)
    if status == NegStatus.AGREED.value and latest is not None:
        assert latest.amount is not None and latest.coverage_hours is not None
        return _say_may_accept(latest.amount, latest.coverage_hours)
    if status == NegStatus.CONFIRMED.value:
        return "Great, thank you for confirming. Goodbye."
    if status == NegStatus.TERMS_CHANGED.value:
        return "I understand — I'll take the updated terms to my client for approval. Goodbye."
    if status == NegStatus.DECLINED_BY_PROVIDER.value:
        return "I understand. I'll let my client know. Goodbye."
    return "Thanks for your time. Goodbye."


def _content(ok: bool, *, say: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": ok}
    if say is not None:
        payload["say"] = say
    payload.update(extra)
    return json.dumps(payload)


_SPECS: dict[str, ToolSpec] = {
    "get_transaction_policy": ToolSpec(
        "get_transaction_policy",
        "Get the client's budget, service date, location and how many counteroffers remain.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "record_offer": ToolSpec(
        "record_offer",
        "Record what the provider just said about price, availability, and terms.",
        {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Price in whole KES, if stated."},
                "available": {"type": "boolean"},
                "availability": {"type": "string"},
                "coverage_hours": {"type": "number"},
                "edited_photos": {"type": "integer"},
                "delivery_days": {"type": "integer"},
                "deposit_required": {"type": "boolean"},
                "provider_refuses_negotiation": {"type": "boolean"},
                "terms": {"type": "string"},
                "provider_words": {"type": "string"},
            },
            "required": ["available", "provider_refuses_negotiation", "provider_words"],
            "additionalProperties": False,
        },
    ),
    "make_counteroffer": ToolSpec(
        "make_counteroffer",
        "Speak a counteroffer. Only succeeds with the exact amount the policy allows next.",
        {
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
    ),
    "record_agreement": ToolSpec(
        "record_agreement",
        "Accept the latest offer. Only succeeds if it is still within the client's budget.",
        {
            "type": "object",
            "properties": {"offer_id": {"type": "integer"}},
            "required": ["offer_id"],
            "additionalProperties": False,
        },
    ),
    "escalate": ToolSpec(
        "escalate",
        "Hand a situation you can't resolve yourself to the client.",
        {
            "type": "object",
            "properties": {
                "trigger": {"type": "string", "enum": sorted(TRIGGER_REASONS)},
                "provider_words": {"type": "string"},
            },
            "required": ["trigger", "provider_words"],
            "additionalProperties": False,
        },
    ),
    "end_call": ToolSpec(
        "end_call",
        "End the call.",
        {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    ),
    "get_approved_terms": ToolSpec(
        "get_approved_terms",
        "Get the price, hours, and terms the client already approved.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "record_confirmation": ToolSpec(
        "record_confirmation",
        "Record whether the provider confirmed the approved terms, or what changed.",
        {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean"},
                "provider_words": {"type": "string"},
                "changed_amount": {"type": "integer"},
                "changed_coverage_hours": {"type": "number"},
                "no_longer_available": {"type": "boolean"},
            },
            "required": ["confirmed", "provider_words"],
            "additionalProperties": False,
        },
    ),
}

NEGOTIATION_TOOLS = (
    "get_transaction_policy",
    "record_offer",
    "make_counteroffer",
    "record_agreement",
    "escalate",
    "end_call",
)
CONFIRMATION_TOOLS = ("get_approved_terms", "record_confirmation", "end_call")


class NegotiationToolExecutor:
    """`ToolExecutor` for one call. `negotiation.kind` selects the tool set and dispatch table."""

    def __init__(self, session: Session, tx: Transaction, negotiation: Negotiation) -> None:
        self._session = session
        self._tx = tx
        self._negotiation = negotiation

    def specs(self) -> Sequence[ToolSpec]:
        names = NEGOTIATION_TOOLS if self._negotiation.kind == "negotiation" else CONFIRMATION_TOOLS
        return tuple(_SPECS[name] for name in names)

    async def execute(self, call: ToolCall) -> ToolOutcome:
        payload = {"tool": call.name, **call.arguments}
        record_event(self._session, self._tx.id, "tool.called", payload)
        handlers = {
            "get_transaction_policy": self._get_transaction_policy,
            "record_offer": self._record_offer,
            "make_counteroffer": self._make_counteroffer,
            "record_agreement": self._record_agreement,
            "escalate": self._escalate,
            "end_call": self._end_call,
            "get_approved_terms": self._get_approved_terms,
            "record_confirmation": self._record_confirmation,
        }
        handler = handlers.get(call.name)
        if handler is None:
            return self._reject(f"unknown tool {call.name}")
        return handler(call.arguments)

    # --- shared -------------------------------------------------------------------------------

    def _reject(self, reason: str, *, say: str | None = None) -> ToolOutcome:
        record_event(self._session, self._tx.id, "tool.rejected", {"reason": reason})
        return ToolOutcome(_content(False, say=say), is_error=True)

    def _latest_offer(self) -> Offer | None:
        return _query_latest_offer(self._session, self._negotiation.id)

    def _end_call(self, args: dict[str, Any]) -> ToolOutcome:
        # Contract 5: "The outcome comes from DB state, never from reason." The model's `reason`
        # is required by the schema so it must justify itself, but it never becomes spoken text.
        say = closing_say(self._session, self._tx, self._negotiation)
        return ToolOutcome(_content(True, say=say), end_call=True)

    # --- negotiation call -----------------------------------------------------------------------

    def _get_transaction_policy(self, args: dict[str, Any]) -> ToolOutcome:
        payload = {
            "service": self._tx.service,
            "service_date_spoken": _speak_date(date.fromisoformat(self._tx.service_date)),
            "location": self._tx.location,
            "max_budget": self._tx.max_budget,
            "currency": self._tx.currency,
            "counteroffers_made": self._negotiation.attempt_count,
            "counteroffers_allowed": self._tx.max_attempts,
            "required_terms": list(REQUIRED_TERMS),
        }
        return ToolOutcome(json.dumps(payload))

    def _amount_confirmed(self, amount: int) -> bool:
        turns = self._session.scalars(
            select(TranscriptTurn.text)
            .where(
                TranscriptTurn.negotiation_id == self._negotiation.id,
                TranscriptTurn.speaker == "provider",
            )
            .order_by(TranscriptTurn.id.desc())
            .limit(3)
        ).all()
        return amount_heard(amount, " ".join(reversed(turns)))

    def _record_offer(self, args: dict[str, Any]) -> ToolOutcome:
        amount = args.get("amount")
        provider_words = str(args["provider_words"])

        if amount is not None and not self._amount_confirmed(amount):
            self._negotiation.price_rejections += 1
            if self._negotiation.price_rejections < 2:
                return self._reject("amount not heard", say="Sorry, could you repeat the price?")
            self._negotiation.escalation_trigger = "unclear_audio"
            self._negotiation.status = NegStatus.ESCALATED.value
            record_event(
                self._session,
                self._tx.id,
                "escalation.raised",
                {"trigger": "unclear_audio", "provider_words": provider_words},
            )
            return self._reject("amount not heard twice", say=SAY_MUST_ESCALATE)

        offer_terms = OfferTerms(
            available=bool(args["available"]),
            amount=amount,
            coverage_hours=args.get("coverage_hours"),
            deposit_required=args.get("deposit_required"),
            provider_refuses_negotiation=bool(args["provider_refuses_negotiation"]),
        )
        evaluation = evaluate_offer(
            offer_terms,
            cap=self._tx.max_budget,
            made=self._negotiation.attempt_count,
            max_attempts=self._tx.max_attempts,
            clarifications=self._negotiation.clarifications,
        )
        has_prior_offer = (
            self._session.scalar(
                select(Offer.id).where(Offer.negotiation_id == self._negotiation.id).limit(1)
            )
            is not None
        )
        offer = Offer(
            negotiation_id=self._negotiation.id,
            amount=amount,
            available=offer_terms.available,
            availability=args.get("availability"),
            coverage_hours=offer_terms.coverage_hours,
            edited_photos=args.get("edited_photos"),
            delivery_days=args.get("delivery_days"),
            deposit_required=offer_terms.deposit_required,
            provider_refuses_negotiation=offer_terms.provider_refuses_negotiation,
            terms=args.get("terms"),
            provider_words=provider_words,
            source="provider_counter" if has_prior_offer else "provider_quote",
            decision=evaluation.decision.value,
        )
        self._session.add(offer)
        self._session.flush()
        record_event(
            self._session,
            self._tx.id,
            "offer.recorded",
            {"offer_id": offer.id, "amount": amount, "decision": evaluation.decision.value},
        )
        record_event(
            self._session,
            self._tx.id,
            "policy.evaluated",
            {"offer_id": offer.id, "decision": evaluation.decision.value},
        )
        # Every new offer supersedes whatever came before it — including a premature AGREED from
        # an earlier offer the provider has now changed (S8: they correct their own price before
        # the call ends). Terminal decisions below set their own status right after this.
        self._negotiation.status = NegStatus.IN_PROGRESS.value

        if evaluation.decision == Decision.UNAVAILABLE:
            self._negotiation.status = NegStatus.UNAVAILABLE.value
            return ToolOutcome(_content(True, decision="UNAVAILABLE", say=SAY_UNAVAILABLE))
        if evaluation.decision == Decision.MUST_ESCALATE:
            self._negotiation.escalation_trigger = evaluation.trigger
            self._negotiation.status = NegStatus.ESCALATED.value
            record_event(
                self._session,
                self._tx.id,
                "escalation.raised",
                {"trigger": evaluation.trigger, "provider_words": provider_words},
            )
            return ToolOutcome(_content(True, decision="MUST_ESCALATE", say=SAY_MUST_ESCALATE))
        if evaluation.decision == Decision.CLARIFY:
            self._negotiation.clarifications += 1
            say = SAY_CLARIFY_PRICE if evaluation.clarify_term == "price" else SAY_CLARIFY_HOURS
            return ToolOutcome(
                _content(True, decision="CLARIFY", clarify_term=evaluation.clarify_term, say=say)
            )
        if evaluation.decision == Decision.MAY_ACCEPT:
            return ToolOutcome(_content(True, decision="MAY_ACCEPT", offer_id=offer.id))
        if evaluation.decision == Decision.COUNTER:
            return ToolOutcome(
                _content(
                    True,
                    decision="COUNTER",
                    offer_id=offer.id,
                    next_counteroffer=evaluation.counter_amount,
                )
            )
        # Decision.STOP_NEGOTIATING: only reached when offer.amount > cap (see evaluate_offer).
        assert amount is not None
        self._negotiation.status = NegStatus.OUTSIDE_AUTHORITY.value
        say = _say_stop_negotiating(self._tx.max_budget, amount)
        return ToolOutcome(_content(True, decision="STOP_NEGOTIATING", say=say))

    def _make_counteroffer(self, args: dict[str, Any]) -> ToolOutcome:
        amount = int(args["amount"])
        latest = self._latest_offer()
        if latest is None or latest.decision != Decision.COUNTER.value:
            return self._reject("no pending counteroffer decision")
        expected = next_counteroffer(
            cap=self._tx.max_budget,
            made=self._negotiation.attempt_count,
            max_attempts=self._tx.max_attempts,
        )
        if expected is None or amount != expected:
            return self._reject(f"counteroffer must be {expected}, not {amount}")
        self._negotiation.attempt_count += 1
        cap_text = format_kes(self._tx.max_budget)
        is_final = self._negotiation.attempt_count >= self._tx.max_attempts
        amount_text = format_kes(amount)
        say = (
            f"I'm not authorized to agree above {cap_text}. Is {cap_text} possible?"
            if is_final
            else f"The client is working with a {cap_text} budget. Could you do {amount_text}?"
        )
        record_event(
            self._session,
            self._tx.id,
            "counteroffer.made",
            {"amount": amount, "attempt_count": self._negotiation.attempt_count},
        )
        return ToolOutcome(_content(True, say=say))

    def _record_agreement(self, args: dict[str, Any]) -> ToolOutcome:
        offer_id = int(args["offer_id"])
        latest = self._latest_offer()
        if latest is None or latest.id != offer_id or latest.decision != Decision.MAY_ACCEPT.value:
            return self._reject("offer_id is not the latest MAY_ACCEPT offer")
        assert latest.amount is not None and latest.coverage_hours is not None
        self._negotiation.status = NegStatus.AGREED.value
        say = _say_may_accept(latest.amount, latest.coverage_hours)
        return ToolOutcome(_content(True, say=say))

    def _escalate(self, args: dict[str, Any]) -> ToolOutcome:
        trigger = str(args["trigger"])
        provider_words = str(args["provider_words"])
        if trigger not in TRIGGER_REASONS:
            return self._reject(f"unknown escalation trigger: {trigger}")
        self._negotiation.escalation_trigger = trigger
        self._negotiation.status = NegStatus.ESCALATED.value
        record_event(
            self._session,
            self._tx.id,
            "escalation.raised",
            {"trigger": trigger, "provider_words": provider_words},
        )
        return ToolOutcome(_content(True, say=SAY_MUST_ESCALATE))

    # --- confirmation call ----------------------------------------------------------------------

    def _latest_approval(self) -> Approval | None:
        return self._session.scalar(
            select(Approval)
            .where(Approval.transaction_id == self._tx.id, Approval.decision == "APPROVED")
            .order_by(Approval.id.desc())
            .limit(1)
        )

    def _get_approved_offer(self) -> Offer | None:
        approval = self._latest_approval()
        return self._session.get(Offer, approval.offer_id) if approval else None

    def _get_approved_terms(self, args: dict[str, Any]) -> ToolOutcome:
        approval = self._latest_approval()
        offer = self._session.get(Offer, approval.offer_id) if approval else None
        provider = self._session.get(Provider, self._negotiation.provider_id)
        payload = {
            "provider_name": provider.name if provider else None,
            "service_date_spoken": _speak_date(date.fromisoformat(self._tx.service_date)),
            "location": self._tx.location,
            "approved_amount": (
                approval.approved_amount
                if approval and approval.approved_amount is not None
                else (offer.amount if offer else None)
            ),
            "coverage_hours": offer.coverage_hours if offer else None,
            "terms": offer.terms if offer else None,
        }
        return ToolOutcome(json.dumps(payload))

    def _record_confirmation(self, args: dict[str, Any]) -> ToolOutcome:
        confirmed = bool(args["confirmed"])
        provider_words = str(args["provider_words"])
        changed_amount = args.get("changed_amount")
        changed_coverage_hours = args.get("changed_coverage_hours")
        no_longer_available = bool(args.get("no_longer_available", False))

        if no_longer_available:
            self._negotiation.status = NegStatus.DECLINED_BY_PROVIDER.value
            offer = Offer(
                negotiation_id=self._negotiation.id,
                amount=None,
                available=False,
                provider_words=provider_words,
                source="confirmation_call",
                decision=Decision.UNAVAILABLE.value,
            )
            self._session.add(offer)
            record_event(
                self._session,
                self._tx.id,
                "offer.recorded",
                {"available": False, "source": "confirmation_call"},
            )
            say = "I understand. I'll let my client know. Goodbye."
            return ToolOutcome(_content(True, say=say))

        if changed_amount is not None or changed_coverage_hours is not None:
            approved = self._get_approved_offer()
            amount = (
                changed_amount
                if changed_amount is not None
                else (approved.amount if approved else None)
            )
            hours = (
                changed_coverage_hours
                if changed_coverage_hours is not None
                else (approved.coverage_hours if approved else None)
            )
            # Contract 5 doesn't specify a decision for a changed confirmation-call offer beyond
            # "re-evaluation and re-approval" — a plain within/above-cap check (not the full
            # evaluate_offer counter/clarify machinery, which doesn't apply to a confirmation call).
            decision = (
                Decision.MAY_ACCEPT.value
                if amount is not None and hours is not None and amount <= self._tx.max_budget
                else Decision.MUST_ESCALATE.value
            )
            offer = Offer(
                negotiation_id=self._negotiation.id,
                amount=amount,
                available=True,
                coverage_hours=hours,
                provider_words=provider_words,
                source="confirmation_call",
                decision=decision,
            )
            self._session.add(offer)
            self._session.flush()
            record_event(
                self._session,
                self._tx.id,
                "offer.recorded",
                {"offer_id": offer.id, "amount": amount, "decision": decision},
            )
            self._negotiation.status = NegStatus.TERMS_CHANGED.value
            say = "I understand — I'll take the updated terms to my client for approval. Goodbye."
            return ToolOutcome(_content(True, decision=decision, say=say))

        if confirmed:
            self._negotiation.status = NegStatus.CONFIRMED.value
            say = "Great, thank you for confirming. Goodbye."
            return ToolOutcome(_content(True, say=say))

        return self._reject("record_confirmation: confirmed=false with no change reported")
