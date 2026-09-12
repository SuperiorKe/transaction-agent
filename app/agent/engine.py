"""Provider-neutral negotiation engine loop.

One model turn per caller message: the model may call tools (policy-gated by the ToolExecutor)
for a bounded number of rounds, then speaks. The engine knows nothing about phones or model
vendors. Issue #4 supplies the real tools, prompts and simulator scenarios.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.conversation import AgentTurn, CallerMessage
from app.llm.base import LLMError, LLMProvider, Message, TextPart, ToolCall, ToolResult, ToolSpec

log = logging.getLogger(__name__)

CALL_CONNECTED = (
    "[The call has connected. The provider picked up and is waiting for you to speak first.]"
)
REPEAT_PLEASE = "Sorry, could you say that again?"
FORCED_GOODBYE = "Sorry, I have to end the call now. I'll pass this to my client. Goodbye."


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool = False
    end_call: bool = False  # the tool decided the call should end after the agent's next words


class ToolExecutor(Protocol):
    def specs(self) -> Sequence[ToolSpec]: ...

    async def execute(self, call: ToolCall) -> ToolOutcome: ...


class NegotiationEngine:
    """A ConversationAgent backed by any LLMProvider and ToolExecutor. One instance per call."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        system_prompt: str,
        tools: ToolExecutor,
        max_tool_rounds: int = 4,
        max_tokens: int = 16000,
    ) -> None:
        self._llm = llm
        self._system_prompt = system_prompt
        self._tools = tools
        self._max_tool_rounds = max_tool_rounds
        self._max_tokens = max_tokens
        self._history: list[Message] = []

    @property
    def history(self) -> tuple[Message, ...]:
        return tuple(self._history)

    async def start(self) -> AgentTurn:
        return await self._run(CALL_CONNECTED)

    async def respond(self, message: CallerMessage) -> AgentTurn:
        return await self._run(message.text)

    async def finish(self, reason: str | None) -> None:
        log.info("call finished: %s", reason)

    async def _run(self, user_text: str) -> AgentTurn:
        self._history.append(Message("user", (TextPart(user_text),)))
        end_call = False

        for _ in range(self._max_tool_rounds + 1):
            try:
                response = await self._llm.generate(
                    system=self._system_prompt,
                    messages=self._history,
                    tools=self._tools.specs(),
                    max_tokens=self._max_tokens,
                )
            except LLMError as exc:
                log.warning("LLM call failed (retryable=%s): %s", exc.retryable, exc)
                if exc.retryable:
                    return self._say(REPEAT_PLEASE, end_call=end_call, note="llm_error_retryable")
                return self._say(FORCED_GOODBYE, end_call=True, note="llm_error")

            if response.stop_reason == "refusal":
                return self._say(FORCED_GOODBYE, end_call=True, note="refusal")

            self._history.append(Message("assistant", response.parts, response.provider_payload))
            if not response.tool_calls:
                return AgentTurn(response.text.strip(), end_call=end_call)

            # All results for one model turn go back in a single user message.
            results = []
            for call in response.tool_calls:
                outcome = await self._execute(call)
                end_call = end_call or outcome.end_call
                results.append(ToolResult(call.id, outcome.content, outcome.is_error))
            self._history.append(Message("user", tuple(results)))

        log.warning("tool round limit (%d) reached", self._max_tool_rounds)
        return self._say(REPEAT_PLEASE, end_call=end_call, note="tool_round_limit")

    def _say(self, text: str, *, end_call: bool, note: str) -> AgentTurn:
        """Return a turn AND record it as an assistant message, so history stays consistent
        with what was actually spoken even on error/refusal/round-limit paths."""
        self._history.append(Message("assistant", (TextPart(text),)))
        return AgentTurn(text, end_call=end_call, note=note)

    async def _execute(self, call: ToolCall) -> ToolOutcome:
        try:
            return await self._tools.execute(call)
        except Exception:  # a broken tool must not crash a live call
            log.exception("tool %s failed", call.name)
            # Details stay in the server log: tool results reach the model, which may speak them.
            return ToolOutcome(f"Tool {call.name} failed.", is_error=True)
