"""web/openapi.json is the committed API contract the owner UI's TypeScript types come from.

If this fails, the API changed without regenerating the snapshot. Run:

    uv run python -m app.openapi_export

then commit web/openapi.json. `npm test` and `npm run build` regenerate the TS types from it.
"""

import json
from pathlib import Path

from app import openapi_export
from app.openapi_export import SNAPSHOT_PATH, render_openapi

REPO_ROOT = Path(__file__).resolve().parent.parent
REGENERATE = "run `uv run python -m app.openapi_export` and commit web/openapi.json"


def test_the_snapshot_lives_at_web_openapi_json():
    assert SNAPSHOT_PATH == REPO_ROOT / "web" / "openapi.json"


def test_the_committed_openapi_snapshot_matches_the_app():
    assert SNAPSHOT_PATH.is_file(), f"web/openapi.json is missing: {REGENERATE}"
    assert SNAPSHOT_PATH.read_text() == render_openapi(), f"web/openapi.json is stale: {REGENERATE}"


def test_the_export_command_writes_the_rendered_snapshot(tmp_path, monkeypatch, capsys):
    """`uv run python -m app.openapi_export` is the fix this file's failures point at."""
    target = tmp_path / "web" / "openapi.json"
    monkeypatch.setattr(openapi_export, "SNAPSHOT_PATH", target)

    openapi_export.main()

    assert target.read_text() == render_openapi()
    assert str(target) in capsys.readouterr().out


def test_rendering_is_deterministic():
    assert render_openapi() == render_openapi()


def test_the_snapshot_covers_the_owner_ui_routes_and_status_contract():
    spec = json.loads(render_openapi())
    owner_ui_paths = {
        "/transactions",
        "/transactions/{transaction_id}",
        "/transactions/{transaction_id}/retry-confirmation",
        "/parse-request",
        "/health",
    }

    assert owner_ui_paths <= set(spec["paths"])
    assert not any(path.startswith("/webhooks") for path in spec["paths"])
    view = spec["components"]["schemas"]["TransactionView"]["properties"]
    assert {"status", "terminal", "status_history"} <= set(view)
