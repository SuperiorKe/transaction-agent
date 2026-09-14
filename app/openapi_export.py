"""Write web/openapi.json, the API contract the owner UI's TypeScript types are generated from.

    uv run python -m app.openapi_export

The app is built offline with no .env and FakeTelephonyProvider, so the snapshot depends only on
the code. tests/test_openapi_snapshot.py fails when the committed file is stale (eng review D2).
"""

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.telephony.fake import FakeTelephonyProvider

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "web" / "openapi.json"


def render_openapi() -> str:
    app = create_app(Settings(_env_file=None), telephony=FakeTelephonyProvider())
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(render_openapi())
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
