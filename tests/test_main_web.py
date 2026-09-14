"""The built owner UI is served from web/dist, checked on every request (eng review D6, D18, D22).

A build made after uvicorn started must be served without a restart, a missing build is a helpful
404 (never a 500), and index.html is never cached so a rebuild can't leave a blank page. Every test
builds into tmp_path: nothing here may touch the real web/dist.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import init_db, make_engine
from app.main import create_app
from app.models import Provider
from app.telephony.fake import FakeTelephonyProvider

TUNNEL = {"cf-connecting-ip": "203.0.113.7"}


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        twilio_account_sid="ACtest",
        twilio_auth_token="token",
        twilio_voice_number="+254200000000",
        webhook_base_url="https://tunnel.example",
        voice_webhook_secret="s3cret",
        **overrides,
    )


def _in_memory_session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    init_db(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(web_dist: Path, **kwargs) -> TestClient:
    # No `with` block: the lifespan would init the real transaction_agent.db.
    app = create_app(
        _settings(), session_factory=_in_memory_session_factory(), web_dist=web_dist, **kwargs
    )
    return TestClient(app)


def _build(web_dist: Path, *, index: str = "<!doctype html><title>owner ui</title>") -> None:
    (web_dist / "assets").mkdir(parents=True, exist_ok=True)
    (web_dist / "index.html").write_text(index)
    (web_dist / "assets" / "app-abc123.js").write_text("console.log('owner ui')")


def test_root_without_a_build_is_a_404_that_says_how_to_build(tmp_path):
    client = _client(tmp_path / "dist")

    response = client.get("/")

    assert response.status_code == 404
    assert "npm run build" in response.text


def test_root_serves_index_html_and_forbids_caching_it(tmp_path):
    dist = tmp_path / "dist"
    _build(dist, index="<!doctype html><title>owner ui marker</title>")
    client = _client(dist)

    response = client.get("/")

    assert response.status_code == 200
    assert "owner ui marker" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_a_build_made_after_the_app_started_is_served_without_a_restart(tmp_path):
    dist = tmp_path / "dist"
    client = _client(dist)
    assert client.get("/").status_code == 404

    _build(dist)

    assert client.get("/").status_code == 200
    asset = client.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert "owner ui" in asset.text


def test_an_asset_requested_before_any_build_is_a_404_not_a_500(tmp_path):
    client = _client(tmp_path / "dist")

    assert client.get("/assets/app-abc123.js").status_code == 404


def test_a_missing_asset_is_a_404(tmp_path):
    dist = tmp_path / "dist"
    _build(dist)
    client = _client(dist)

    assert client.get("/assets/does-not-exist.js").status_code == 404


def test_the_ui_is_not_reachable_through_the_tunnel(tmp_path):
    dist = tmp_path / "dist"
    _build(dist)
    client = _client(dist)

    assert client.get("/", headers=TUNNEL).status_code == 403
    assert client.get("/assets/app-abc123.js", headers=TUNNEL).status_code == 403


def test_the_api_still_works_with_no_ui_built(tmp_path):
    client = _client(tmp_path / "dist")

    assert client.get("/health").json() == {"ok": True}


def test_create_app_dials_through_an_injected_telephony_provider(tmp_path):
    """The Playwright E2E server runs on FakeTelephonyProvider, so it can't reach Twilio (D28)."""
    telephony = FakeTelephonyProvider()
    session_factory = _in_memory_session_factory()
    with session_factory() as db:
        db.add(
            Provider(
                name="Studio A", phone="+254712345678", location="Nairobi", priority=1, active=True
            )
        )
        db.commit()
    app = create_app(
        _settings(),
        session_factory=session_factory,
        web_dist=tmp_path / "dist",
        telephony=telephony,
    )
    client = TestClient(app)
    tomorrow = datetime.now(ZoneInfo("Africa/Nairobi")).date() + timedelta(days=1)
    tx = client.post(
        "/transactions",
        json={
            "request": "Photographer for tomorrow in Nairobi. Maximum KES 20,000.",
            "service": "photography",
            "service_date": tomorrow.isoformat(),
            "location": "Nairobi",
            "max_budget": 20000,
            "max_attempts": 2,
        },
    ).json()

    response = client.post(f"/transactions/{tx['id']}/start")

    assert response.status_code == 202
    assert telephony.placed_calls == ["+254712345678"]
