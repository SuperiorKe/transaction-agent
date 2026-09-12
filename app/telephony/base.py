"""Telephony provider interface.

How the operations map onto a webhook-driven voice platform:
- start a call          -> `place_call`
- answer / caller input -> the platform posts a callback; `parse_callback` turns it into a CallEvent
- speak + collect input -> `render(AgentTurn)` with `end_call=False`
- continue conversation -> the next callback repeats the cycle
- hang up               -> `render(AgentTurn)` with `end_call=True`, or `hang_up` out of band
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.conversation import AgentTurn


@dataclass(frozen=True)
class CallAnswered:
    session_id: str
    remote_number: str | None = None


@dataclass(frozen=True)
class CallerSpoke:
    session_id: str
    text: str


@dataclass(frozen=True)
class CallerSilent:
    """The caller's turn produced no usable speech (silence, empty recording, no transcript)."""

    session_id: str


@dataclass(frozen=True)
class CallEnded:
    session_id: str
    reason: str | None = None
    duration_seconds: int | None = None


CallEvent = CallAnswered | CallerSpoke | CallerSilent | CallEnded


@dataclass(frozen=True)
class PlacedCall:
    provider_call_id: str


@dataclass(frozen=True)
class ProviderReply:
    """HTTP response body for the provider's callback."""

    body: str
    media_type: str


class TelephonyError(Exception):
    pass


class TelephonyProvider(Protocol):
    async def place_call(self, to_number: str) -> PlacedCall: ...

    async def parse_callback(self, form: Mapping[str, str]) -> CallEvent: ...

    def render(self, turn: AgentTurn) -> ProviderReply: ...

    def acknowledge(self) -> ProviderReply:
        """Reply to callbacks that need no instructions (e.g. the call-ended notification)."""
        ...

    async def hang_up(self, provider_call_id: str) -> None: ...
