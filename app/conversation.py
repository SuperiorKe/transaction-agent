"""Domain-level conversation types shared by the agent and the phone layer.

Neither side imports the other. A ConversationAgent turns CallerMessages into AgentTurns; a
TelephonyProvider turns AgentTurns into whatever its platform needs (voice XML, JSON, ...).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CallerMessage:
    """What the person on the phone said, as text."""

    text: str


@dataclass(frozen=True)
class AgentTurn:
    """What the agent says next, and whether the call ends after saying it."""

    say: str
    end_call: bool = False
    note: str | None = None  # diagnostic reason, e.g. "llm_error"; never spoken


class ConversationAgent(Protocol):
    async def start(self) -> AgentTurn:
        """First turn after the call connects (the agent speaks first on outbound calls)."""
        ...

    async def respond(self, message: CallerMessage) -> AgentTurn: ...

    async def finish(self, reason: str | None) -> None:
        """The call is over (hung up, dropped, or ended by the agent)."""
        ...
