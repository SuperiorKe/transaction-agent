import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import NegotiationEngine
from app.agent.prompts import NEGOTIATION_SYSTEM_PROMPT
from app.agent.session import NegotiationCall
from app.agent.tools import NegotiationToolExecutor
from app.calls import AgentFactory, CallCoordinator
from app.config import Settings, get_settings
from app.conversation import AgentTurn, CallerMessage, ConversationAgent
from app.db import SessionLocal, init_db
from app.llm.anthropic import AnthropicLLMProvider
from app.llm.base import LLMProvider
from app.middleware import LocalOnlyOwnerRoutes
from app.models import Negotiation, Transaction
from app.routes.voice import build_voice_router
from app.telephony.twilio import build_twilio_provider

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
PREFLIGHT_GREETING = (
    "Hello, I'm an AI assistant. This is a brief phone connection test. Can you hear me?"
)


class _PreflightCallAgent:
    """Safe fallback for a call whose session id matches no negotiation.

    Covers two cases until #6's orchestrator exists: a plain phone-path smoke test, and a
    genuinely unknown/inbound call (which #6 will instead answer with a polite rejection).
    """

    async def start(self) -> AgentTurn:
        return AgentTurn(PREFLIGHT_GREETING)

    async def respond(self, _message: CallerMessage) -> AgentTurn:
        return AgentTurn(
            "Thanks, I can hear you. I'll end the test call now. Goodbye.", end_call=True
        )

    async def finish(self, _reason: str | None) -> None:
        return None


def _default_llm_factory(settings: Settings) -> LLMProvider:
    return AnthropicLLMProvider(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        effort=settings.anthropic_effort,
        refusal_fallback=settings.anthropic_refusal_fallback,
    )


def _build_negotiation_agent_factory(
    settings: Settings,
    session_factory: Callable[[], Session],
    llm_factory: Callable[[Settings], LLMProvider],
) -> AgentFactory:
    """Resolve a call's session id to the negotiation `scripts/negotiate_call.py` placed it for.

    A stand-in for #6's orchestrator, which will own this mapping (and its own retry window for
    the same race) once it exists: the callback can arrive a beat before the placing script's
    commit is visible, so a short synchronous retry covers it.
    """

    def factory(session_id: str) -> ConversationAgent:
        db = session_factory()
        negotiation = None
        for attempt in range(5):
            negotiation = db.scalar(
                select(Negotiation).where(Negotiation.provider_call_id == session_id)
            )
            if negotiation is not None or attempt == 4:
                break
            time.sleep(0.2)

        if negotiation is None:
            db.close()
            log.warning("no negotiation registered for call %s; using preflight stub", session_id)
            return _PreflightCallAgent()

        tx = db.get(Transaction, negotiation.transaction_id)
        tools = NegotiationToolExecutor(db, tx, negotiation)
        engine = NegotiationEngine(
            llm_factory(settings), system_prompt=NEGOTIATION_SYSTEM_PROMPT, tools=tools
        )
        return NegotiationCall(
            db, tx, negotiation, engine, hard_end_seconds=settings.call_hard_end_seconds
        )

    return factory


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    llm_factory: Callable[[Settings], LLMProvider] = _default_llm_factory,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    telephony = build_twilio_provider(settings)
    agent_factory = _build_negotiation_agent_factory(settings, session_factory, llm_factory)
    coordinator = CallCoordinator(telephony, agent_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        try:
            yield
        finally:
            await telephony.aclose()

    app = FastAPI(title="Transaction Agent", lifespan=lifespan)
    app.add_middleware(LocalOnlyOwnerRoutes)
    # Both Twilio's number-level Voice webhook and the <Gather> action use this exact
    # secret path.  The provider's outbound-call Url and completed-status callback do too.
    app.include_router(build_voice_router(coordinator, settings.voice_webhook_secret))

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
