"""Connects telephony callbacks to conversation agents, one agent per call session.

Provider-agnostic: it sees CallEvents and AgentTurns only.
"""

import asyncio
import logging
from collections import OrderedDict
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
UNEXPECTED_ERROR_GOODBYE = (
    "Sorry, something went wrong on my end. I'll pass this to my client. Goodbye."
)

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
        max_ended_sessions_remembered: int = 1000,
    ) -> None:
        self._telephony = telephony
        self._agent_factory = agent_factory
        self._max_silent_turns = max_silent_turns
        self._sessions: dict[str, _Session] = {}
        # Bounded LRU of session ids whose call has already ended, so a duplicate/replayed
        # webhook for it (providers redeliver on a slow or failed response) is ignored instead
        # of silently starting a brand-new negotiation for a call that no longer exists.
        self._ended_sessions: OrderedDict[str, None] = OrderedDict()
        self._max_ended_sessions = max_ended_sessions_remembered

    @property
    def active_sessions(self) -> frozenset[str]:
        return frozenset(self._sessions)

    def render_end_call(self, message: str = UNEXPECTED_ERROR_GOODBYE) -> ProviderReply:
        """A provider-appropriate reply that ends the call, for callers (e.g. the voice route)
        that hit an error handle_callback itself can't recover from."""
        return self._telephony.render(AgentTurn(message, end_call=True))

    def _tombstone(self, session_id: str) -> None:
        self._ended_sessions[session_id] = None
        self._ended_sessions.move_to_end(session_id)
        if len(self._ended_sessions) > self._max_ended_sessions:
            self._ended_sessions.popitem(last=False)

    async def handle_callback(self, form: Mapping[str, str]) -> ProviderReply:
        event = await self._telephony.parse_callback(form)

        if isinstance(event, CallEnded):
            session = self._sessions.pop(event.session_id, None)
            if session is not None:
                # Serialize with any turn still in flight for this session: a hang-up callback
                # can otherwise land while a slow LLM/tool round is still running.
                async with session.lock:
                    await session.agent.finish(event.reason)
            self._tombstone(event.session_id)
            return self._telephony.acknowledge()

        if event.session_id in self._ended_sessions:
            log.info(
                "ignoring %s for already-ended session %s", type(event).__name__, event.session_id
            )
            return self._telephony.acknowledge()

        session = self._sessions.get(event.session_id)
        is_new = session is None
        if session is None:
            session = _Session(agent=self._agent_factory(event.session_id))
            self._sessions[event.session_id] = session

        try:
            async with session.lock:
                turn = await self._next_turn(session, event, is_new)
        except Exception:
            # A crashed turn must not leave a dead session registered: without this, a retry
            # for the same call is misrouted into the caller-silent branch forever.
            self._sessions.pop(event.session_id, None)
            raise
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
