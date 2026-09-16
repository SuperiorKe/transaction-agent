"""Serve the owner UI end to end on a throwaway database, for web/e2e (Playwright).

    uv run python -m scripts.e2e_server --port 8123 --web-dist web/dist-e2e    # from the repo root

Isolation:
- DATABASE_URL points at a temp SQLite file before anything under app/ is imported, because
  app/db.py builds its engine at import time. build_app() refuses to start if the engine points
  anywhere else, and the temp directory is removed on exit.
- The app is built with Settings(_env_file=None).
- It dials through FakeTelephonyProvider, so no phone can ring even if /start were called.
- It serves web/dist-e2e, never the web/dist a running demo server uses.

Importing app.main still runs its module-level create_app() against the real .env. That builds
a Twilio client in memory which this server never uses.

This script adds one test-only route, POST /__e2e/transactions/{id}/transition. It calls
app/states.py's transition(), so status.changed rows are written exactly as in production. The
route exists only here, never in app.main.
"""

import argparse
import atexit
import os
import shutil
import tempfile
from pathlib import Path

_DB_DIR = tempfile.mkdtemp(prefix="transaction-agent-e2e-")
atexit.register(shutil.rmtree, _DB_DIR, ignore_errors=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_DIR}/e2e.db"

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Transaction  # noqa: E402
from app.states import IllegalTransition, transition  # noqa: E402
from app.telephony.fake import FakeTelephonyProvider  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


class TransitionBody(BaseModel):
    target: str


def build_app(web_dist: Path) -> FastAPI:
    database = engine.url.database or ""
    if not database.startswith(_DB_DIR):
        raise SystemExit(f"refusing to run the e2e server against {engine.url}, not {_DB_DIR}")
    init_db()
    app = create_app(Settings(_env_file=None), telephony=FakeTelephonyProvider(), web_dist=web_dist)

    @app.post("/__e2e/transactions/{transaction_id}/transition")
    def e2e_transition(transaction_id: str, body: TransitionBody) -> dict[str, str]:
        with SessionLocal() as db:
            tx = db.get(Transaction, transaction_id)
            if tx is None:
                raise HTTPException(404, detail="transaction not found")
            try:
                transition(db, tx, body.target, reason="e2e")
            except (IllegalTransition, ValueError) as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            db.commit()
            return {"status": tx.status}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--web-dist", type=Path, default=REPO_ROOT / "web" / "dist-e2e")
    args = parser.parse_args()
    print(f"e2e database: {_DB_DIR}/e2e.db, serving {args.web_dist}", flush=True)
    uvicorn.run(build_app(args.web_dist), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
