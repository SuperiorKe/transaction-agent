import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import Provider
from app.seed import SeedError, seed_providers

# Fake numbers on the +2541 range so the #9 secret check (+2547…) never matches test data.
PHONE_1 = "+254100000001"
PHONE_2 = "+254100000002"


def make_settings(**overrides) -> Settings:
    values = {
        "provider_1_name": "Provider One",
        "provider_1_phone": PHONE_1,
        "provider_2_name": "Provider Two",
        "provider_2_phone": PHONE_2,
        "provider_location": "Nairobi",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def providers_by_priority(session) -> dict[int, Provider]:
    return {p.priority: p for p in session.scalars(select(Provider))}


def test_seed_twice_keeps_exactly_two_providers(session):
    seed_providers(session, make_settings())
    seed_providers(session, make_settings())

    rows = providers_by_priority(session)
    assert sorted(rows) == [1, 2]
    assert rows[1].phone == PHONE_1
    assert rows[2].phone == PHONE_2
    assert all(p.active and p.service == "photography" for p in rows.values())


def test_seed_swapping_phones_updates_in_place(session):
    seed_providers(session, make_settings())
    ids_before = {prio: p.id for prio, p in providers_by_priority(session).items()}

    seed_providers(session, make_settings(provider_1_phone=PHONE_2, provider_2_phone=PHONE_1))

    rows = providers_by_priority(session)
    assert {prio: p.id for prio, p in rows.items()} == ids_before
    assert rows[1].phone == PHONE_2
    assert rows[2].phone == PHONE_1


@pytest.mark.parametrize(
    "bad_phone", ["0712345678", "+15551234567", "+25410000001", "254100000001"]
)
def test_seed_rejects_non_kenyan_e164(session, bad_phone):
    with pytest.raises(SeedError, match="PROVIDER_1_PHONE"):
        seed_providers(session, make_settings(provider_1_phone=bad_phone))
    assert providers_by_priority(session) == {}


def test_seed_requires_both_providers(session):
    with pytest.raises(SeedError, match="PROVIDER_2_NAME and PROVIDER_2_PHONE"):
        seed_providers(session, make_settings(provider_2_phone=""))
