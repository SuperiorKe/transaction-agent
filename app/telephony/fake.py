"""In-memory telephony provider for automated tests and local runs without a phone number.

Callbacks are plain dicts built with `answered`, `said`, `silence` and `ended`; replies are
JSON so tests can assert on exactly what would have been spoken.
"""

import json
from collections.abc import Mapping

from app.conversation import AgentTurn
from app.telephony.base import (
    CallAnswered,
    CallEnded,
    CallerSilent,
    CallerSpoke,
    CallEvent,
    PlacedCall,
    ProviderReply,
    TelephonyError,
)


def answered(session_id: str, remote_number: str | None = None) -> dict[str, str]:
    form = {"event": "answered", "session_id": session_id}
    if remote_number:
        form["remote_number"] = remote_number
    return form


def said(session_id: str, text: str) -> dict[str, str]:
    return {"event": "spoke", "session_id": session_id, "text": text}


def silence(session_id: str) -> dict[str, str]:
    return {"event": "silence", "session_id": session_id}


def ended(session_id: str, reason: str = "hangup") -> dict[str, str]:
    return {"event": "ended", "session_id": session_id, "reason": reason}


class FakeTelephonyProvider:
    def __init__(self) -> None:
        self.placed_calls: list[str] = []
        self.hung_up: list[str] = []
        self.rendered: list[AgentTurn] = []

    async def place_call(self, to_number: str) -> PlacedCall:
        self.placed_calls.append(to_number)
        return PlacedCall(provider_call_id=f"fake-{len(self.placed_calls)}")

    async def parse_callback(self, form: Mapping[str, str]) -> CallEvent:
        session_id = form.get("session_id", "")
        match form.get("event"):
            case "answered":
                return CallAnswered(session_id, form.get("remote_number"))
            case "spoke":
                return CallerSpoke(session_id, form.get("text", ""))
            case "silence":
                return CallerSilent(session_id)
            case "ended":
                return CallEnded(session_id, form.get("reason"))
        raise TelephonyError(f"unknown fake callback: {dict(form)}")

    def render(self, turn: AgentTurn) -> ProviderReply:
        self.rendered.append(turn)
        body = json.dumps({"say": turn.say, "end_call": turn.end_call})
        return ProviderReply(body=body, media_type="application/json")

    def acknowledge(self) -> ProviderReply:
        return ProviderReply(body="{}", media_type="application/json")

    async def hang_up(self, provider_call_id: str) -> None:
        self.hung_up.append(provider_call_id)
