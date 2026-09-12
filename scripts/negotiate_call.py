"""Place a real outbound call and let it negotiate, ahead of #6's full orchestrator/API.

Run this while `uv run uvicorn app.main:app` (with its cloudflared tunnel and Twilio webhook)
is already up. This script only creates the Transaction/Provider/Negotiation rows and dials —
the actual conversation runs inside the *running app* once Twilio starts posting callbacks to
its webhook, which resolves the call by the `provider_call_id` this script writes.

There's no `POST /transactions` yet (#6), so this stands in for it: a fixed cap/attempts/date,
no state-machine transitions, no next-provider fallback. Good enough to prove a real call
negotiates; not a replacement for #6's orchestrator.

Usage:
    uv run python -m scripts.negotiate_call --to +2547XXXXXXXX
    uv run python -m scripts.negotiate_call --to +2547XXXXXXXX --cap 20000 --attempts 2
"""

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Negotiation, Provider, Transaction
from app.telephony.twilio import build_twilio_provider


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="Provider phone, E.164, e.g. +2547XXXXXXXX")
    parser.add_argument("--cap", type=int, default=20000, help="Hard budget cap, whole KES")
    parser.add_argument("--attempts", type=int, default=2, help="Max counteroffers")
    parser.add_argument("--date", default="2026-09-14", help="Service date, ISO")
    parser.add_argument("--location", default="Nairobi")
    args = parser.parse_args()

    init_db()
    settings = get_settings()

    with SessionLocal() as db:
        provider = db.scalar(select(Provider).where(Provider.phone == args.to))
        if provider is None:
            provider = Provider(
                name=f"Provider {args.to[-4:]}",
                phone=args.to,
                location=args.location,
                priority=1,
            )
            db.add(provider)
            db.flush()

        tx = Transaction(
            request=(
                f"Photographer for {args.date} in {args.location}. "
                f"Maximum KES {args.cap:,}. Negotiate {args.attempts} times; "
                f"never agree above KES {args.cap:,} without my approval."
            ),
            service_date=args.date,
            location=args.location,
            max_budget=args.cap,
            max_attempts=args.attempts,
            current_provider_id=provider.id,
        )
        db.add(tx)
        db.flush()

        negotiation = Negotiation(transaction_id=tx.id, provider_id=provider.id, kind="negotiation")
        db.add(negotiation)
        db.flush()

        telephony = build_twilio_provider(settings)
        try:
            placed = await telephony.place_call(provider.phone)
        finally:
            await telephony.aclose()

        negotiation.provider_call_id = placed.provider_call_id
        db.commit()

        print(f"transaction {tx.id}")
        print(f"negotiation {negotiation.id}")
        print(f"call sid    {placed.provider_call_id}")
        print("Watch the running app's logs (and transcript_turns/audit_events) for the call.")


if __name__ == "__main__":
    asyncio.run(main())
