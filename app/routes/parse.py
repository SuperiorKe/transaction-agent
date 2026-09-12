"""POST /parse-request: turn free text into a ParsedRequest via a single strict LLM tool call."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.config import Settings
from app.llm.base import LLMError, LLMProvider, Message, TextPart, ToolSpec
from app.schemas import ParsedRequest, ParseRequestBody

log = logging.getLogger(__name__)

SUBMIT_TOOL = ToolSpec(
    "submit_parsed_request",
    "Submit the structured photography request extracted from the client's free text.",
    {
        "type": "object",
        "properties": {
            "service": {"type": "string", "enum": ["photography", "unsupported"]},
            "service_date": {
                "type": ["string", "null"],
                "description": "ISO date (YYYY-MM-DD), or null if it can't be resolved.",
            },
            "date_text": {
                "type": ["string", "null"],
                "description": "The client's own words for the date, if any (e.g. 'Monday').",
            },
            "location": {"type": ["string", "null"]},
            "max_budget": {"type": ["integer", "null"], "description": "Whole KES."},
            "max_attempts": {
                "type": "integer",
                "description": "Negotiation attempts allowed, 0-2. Default 2 if unstated.",
            },
        },
        "required": [
            "service",
            "service_date",
            "date_text",
            "location",
            "max_budget",
            "max_attempts",
        ],
        "additionalProperties": False,
    },
)

SYSTEM_PROMPT = (
    "Extract a structured service request from the client's own words. Only 'photography' is "
    "supported; anything else is service='unsupported'. Resolve relative dates ('Monday', 'next "
    "week') against the date given in the message into an ISO service_date; use null if you can't "
    "resolve one confidently. max_attempts defaults to 2 when the client doesn't state a number, "
    "clamped to the range 0-2. Call submit_parsed_request exactly once with your best extraction. "
    "Never invent a budget, date, or location that wasn't stated or clearly implied."
)


def build_parse_router(
    llm_factory: Callable[[], LLMProvider],
    settings: Settings,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["parse"])

    @router.post("/parse-request", response_model=ParsedRequest)
    async def parse_request(body: ParseRequestBody) -> ParsedRequest:
        today = now().astimezone(ZoneInfo(settings.timezone)).date()
        user_text = (
            f"Today's date is {today.isoformat()} ({settings.timezone}).\n\n"
            f"Client request: {body.text}"
        )
        llm = llm_factory()
        try:
            response = await llm.generate(
                system=SYSTEM_PROMPT,
                messages=[Message("user", (TextPart(user_text),))],
                tools=[SUBMIT_TOOL],
                max_tokens=1024,
            )
        except LLMError as exc:
            raise HTTPException(502, detail=f"LLM error: {exc}") from exc

        call = next((c for c in response.tool_calls if c.name == "submit_parsed_request"), None)
        if call is None:
            raise HTTPException(502, detail="LLM did not call submit_parsed_request")

        return _validate(call.arguments, today)

    return router


def _validate(args: dict[str, Any], today: date) -> ParsedRequest:
    service = (
        args.get("service")
        if args.get("service") in ("photography", "unsupported")
        else "unsupported"
    )

    max_attempts = args.get("max_attempts")
    max_attempts = 2 if not isinstance(max_attempts, int) else max(0, min(2, max_attempts))

    service_date: str | None = args.get("service_date")
    if isinstance(service_date, str):
        try:
            parsed = date.fromisoformat(service_date)
        except ValueError:
            parsed = None
        if parsed is None or not (today <= parsed <= today + timedelta(days=90)):
            service_date = None
    else:
        service_date = None

    location = args.get("location")
    location = location if isinstance(location, str) and location.strip() else None

    max_budget = args.get("max_budget")
    max_budget = (
        max_budget if isinstance(max_budget, int) and 1000 <= max_budget <= 1_000_000 else None
    )

    date_text = args.get("date_text")
    date_text = date_text if isinstance(date_text, str) and date_text.strip() else None

    missing: list[Any] = []
    if service_date is None:
        missing.append("service_date")
    if location is None:
        missing.append("location")
    if max_budget is None:
        missing.append("max_budget")

    return ParsedRequest(
        service=service,
        service_date=service_date,
        date_text=date_text,
        location=location,
        max_budget=max_budget,
        max_attempts=max_attempts,
        missing=missing,
    )
