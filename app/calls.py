"""Connects telephony callbacks to conversation agents, one agent per call session.

Provider-agnostic: it sees CallEvents and AgentTurns only.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.conversation import AgentTurn, CallerMessage, ConversationAgent
from app.telephony.base import (
    CallAnswered,
    CallEnded,
    CallerSilent,
    CallerSpoke,
    ProviderReply,
    TelephonyProvider,
)

log = logging.getLogger(__name__)

DIDNT_CATCH = "Sorry, I didn't catch that. Could you say it again?"
SILENCE_GOODBYE = "I can't hear you, so I'll end the call now. Goodbye."

AgentFactory = Callable[[str], ConversationAgent]  # session_id -> agent for that call


@dataclass
class _Session:
    agent: ConversationAgent
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    silent_turns: int = 0


class CallCoordinator:
    def __init__(
        self,
        telephony: TelephonyProvider,
        agent_factory: AgentFactory,
        *,
        max_silent_turns: int = 2,
    ) -> None:
        self._telephony = telephony
        self._agent_factory = agent_factory
        self._max_silent_turns = max_silent_turns
        self._sessions: dict[str, _Session] = {}

    @property
    def active_sessions(self) -> frozenset[str]:
        return frozenset(self._sessions)

    async def handle_callback(self, form: Mapping[str, str]) -> ProviderReply:
        event = await self._telephony.parse_callback(form)

        if isinstance(event, CallEnded):
            session = self._sessions.pop(event.session_id, None)
            if session is not None:
                await session.agent.finish(event.reason)
            return self._telephony.acknowledge()

        session = self._sessions.get(event.session_id)
        is_new = session is None
        if session is None:
            session = _Session(agent=self._agent_factory(event.session_id))
            self._sessions[event.session_id] = session

        async with session.lock:
            turn = await self._next_turn(session, event, is_new)
        return self._telephony.render(turn)

    async def _next_turn(
        self, session: _Session, event: CallAnswered | CallerSpoke | CallerSilent, is_new: bool
    ) -> AgentTurn:
        if isinstance(event, CallerSpoke) and event.text.strip():
            session.silent_turns = 0
            return await session.agent.respond(CallerMessage(event.text.strip()))

        if is_new:  # first event of the call: the agent speaks first
            return await session.agent.start()

        # Silence, an empty transcript, or a repeat "answered" event for a live call
        # (Africa's Talking posts one when a recording captured nothing).
        session.silent_turns += 1
        if session.silent_turns >= self._max_silent_turns:
            return AgentTurn(SILENCE_GOODBYE, end_call=True, note="caller_silent")
        return AgentTurn(DIDNT_CATCH, note="caller_silent")
