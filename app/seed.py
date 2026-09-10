"""Seed the two consenting test providers from `.env`. Run: `uv run python -m app.seed`."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.models import Provider

KENYA_E164 = re.compile(r"^\+254\d{9}$")


class SeedError(ValueError):
    pass


def provider_specs(settings: Settings) -> list[dict[str, Any]]:
    pairs = [
        (settings.provider_1_name, settings.provider_1_phone),
        (settings.provider_2_name, settings.provider_2_phone),
    ]
    specs = []
    for priority, (name, phone) in enumerate(pairs, start=1):
        if not name or not phone:
            raise SeedError(f"PROVIDER_{priority}_NAME and PROVIDER_{priority}_PHONE must be set")
        if not KENYA_E164.match(phone):
            raise SeedError(f"PROVIDER_{priority}_PHONE must be E.164 Kenyan, like +254XXXXXXXXX")
        specs.append(
            {
                "name": name,
                "phone": phone,
                "priority": priority,
                "location": settings.provider_location,
            }
        )
    return specs


def seed_providers(session: Session, settings: Settings) -> list[Provider]:
    """Upsert providers by priority, so re-running never duplicates rows."""
    specs = provider_specs(settings)
    existing = {p.priority: p for p in session.scalars(select(Provider))}

    # Park the phones we're about to overwrite, so swapping numbers between priorities
    # can't trip the UNIQUE constraint mid-update.
    for spec in specs:
        if provider := existing.get(spec["priority"]):
            provider.phone = f"parked-{provider.id}"
    session.flush()

    providers = []
    for spec in specs:
        provider = existing.get(spec["priority"])
        if provider is None:
            provider = Provider(service="photography", **spec)
            session.add(provider)
        else:
            for field, value in spec.items():
                setattr(provider, field, value)
        provider.active = True
        providers.append(provider)
    session.commit()
    return providers


def main() -> None:
    init_db()
    with SessionLocal() as session:
        try:
            providers = seed_providers(session, get_settings())
        except SeedError as exc:
            raise SystemExit(f"seed failed: {exc}") from exc
        for p in providers:
            print(f"provider {p.priority}: {p.name} (phone ending {p.phone[-3:]})")


if __name__ == "__main__":
    main()
