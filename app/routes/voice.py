"""Voice webhook route, independent of the telephony provider behind the coordinator.

The provider posts form-encoded callbacks to `/webhooks/voice/{secret}`. The secret in the path
is the authentication, so a wrong or unconfigured secret returns 404 and reveals nothing.
"""

import hmac
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response

from app.calls import CallCoordinator


def build_voice_router(coordinator: CallCoordinator, webhook_secret: str) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/voice/{secret}", include_in_schema=False)
    async def voice_callback(secret: str, request: Request) -> Response:
        if not webhook_secret or not hmac.compare_digest(secret, webhook_secret):
            raise HTTPException(status_code=404)
        form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
        reply = await coordinator.handle_callback(form)
        return Response(content=reply.body, media_type=reply.media_type)

    return router
