"""Voice webhook route, independent of the telephony provider behind the coordinator.

The provider posts form-encoded callbacks to `/webhooks/voice/{secret}`. The secret in the path
is the authentication, so a wrong or unconfigured secret returns 404 and reveals nothing.
"""

import hmac
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response

from app.calls import CallCoordinator
from app.telephony.base import TelephonyError

log = logging.getLogger(__name__)


def build_voice_router(coordinator: CallCoordinator, webhook_secret: str) -> APIRouter:
    router = APIRouter()
    expected = webhook_secret.encode()

    @router.post("/webhooks/voice/{secret}", include_in_schema=False)
    async def voice_callback(secret: str, request: Request) -> Response:
        # Compare bytes: hmac.compare_digest raises TypeError for non-ASCII str input.
        if not expected or not hmac.compare_digest(secret.encode(), expected):
            raise HTTPException(status_code=404)
        body = (await request.body()).decode("utf-8", errors="replace")
        try:
            reply = await coordinator.handle_callback(dict(parse_qsl(body, keep_blank_values=True)))
        except TelephonyError as exc:
            # The exception text stays server-side: whoever holds the webhook secret shouldn't
            # also get parser internals back in the response.
            log.warning("voice callback rejected: %s", exc)
            raise HTTPException(status_code=400, detail="invalid callback") from exc
        except Exception:
            # A bug anywhere in the call-handling path must still end the call the way the
            # rest of the system does (a spoken goodbye + hangup), not a bare 500 that leaves
            # the provider and the session in an undefined state.
            log.exception("voice callback failed")
            reply = coordinator.render_end_call()
        return Response(content=reply.body, media_type=reply.media_type)

    return router
