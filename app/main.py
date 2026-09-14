import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.agent.engine import NegotiationEngine
from app.agent.prompts import NEGOTIATION_SYSTEM_PROMPT
from app.agent.session import NegotiationCall
from app.agent.tools import NegotiationToolExecutor
from app.calls import AgentFactory, CallCoordinator
from app.config import Settings, get_settings
from app.conversation import ConversationAgent
from app.db import SessionLocal, init_db
from app.llm.anthropic import AnthropicLLMProvider
from app.llm.base import LLMProvider
from app.middleware import LocalOnlyOwnerRoutes
from app.models import Transaction
from app.orchestrator import OrchestratedCall, UnrecognizedCallAgent, resolve_negotiation
from app.routes.parse import build_parse_router
from app.routes.transactions import build_transactions_router
from app.routes.voice import build_voice_router
from app.telephony.base import TelephonyProvider
from app.telephony.twilio import build_twilio_provider

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class _BuiltAssets(StaticFiles):
    """StaticFiles for web/dist/assets that is a 404 until the first build, not a 500.

    Starlette checks the directory once and raises while it's missing; the owner UI may be built
    after uvicorn starts, so check on every request and keep Starlette's own path handling.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.directory is None or not Path(self.directory).is_dir():
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


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
    telephony: TelephonyProvider,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AgentFactory:
    """The callback -> orchestrator mapping (#6): resolve a call's session id to the Negotiation
    `app/orchestrator.py::place_call` (driven by `POST /transactions/{id}/start`) created it for,
    and wrap the real negotiation agent with `OrchestratedCall` so `on_call_answered`/
    `on_call_ended` run around it. Replaces PR #15's stand-in and `scripts/negotiate_call.py`,
    both of which poked rows into the DB and dialed without ever going through the state machine.
    """

    def factory(session_id: str) -> ConversationAgent:
        resolved = resolve_negotiation(session_factory, session_id)
        if resolved is None:
            log.warning("no negotiation registered for call %s", session_id)
            return UnrecognizedCallAgent(session_factory)
        db, negotiation = resolved

        tx = db.get(Transaction, negotiation.transaction_id)
        tools = NegotiationToolExecutor(db, tx, negotiation)
        engine = NegotiationEngine(
            llm_factory(settings), system_prompt=NEGOTIATION_SYSTEM_PROMPT, tools=tools
        )
        inner = NegotiationCall(
            db, tx, negotiation, engine, hard_end_seconds=settings.call_hard_end_seconds
        )
        return OrchestratedCall(
            inner,
            db,
            tx,
            negotiation,
            telephony=telephony,
            max_calls_per_day=settings.max_calls_per_day,
            now=now,
        )

    return factory


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    llm_factory: Callable[[Settings], LLMProvider] = _default_llm_factory,
    web_dist: Path = WEB_DIST,
    telephony: TelephonyProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    telephony = telephony or build_twilio_provider(settings)
    agent_factory = _build_negotiation_agent_factory(
        settings, session_factory, llm_factory, telephony
    )
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
    app.include_router(build_transactions_router(session_factory, telephony, settings))
    app.include_router(build_parse_router(lambda: llm_factory(settings), settings))

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    # Always registered and checked per request, so `npm run build` needs no uvicorn restart.
    app.mount(
        "/assets", _BuiltAssets(directory=web_dist / "assets", check_dir=False), name="assets"
    )

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        index_html = web_dist / "index.html"
        if not index_html.is_file():
            return PlainTextResponse(
                "The owner UI isn't built yet. Run `npm run build` in web/.", status_code=404
            )
        # Assets are content-hashed and a rebuild deletes the old ones, so a cached index.html
        # would point at files that no longer exist.
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
