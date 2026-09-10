"""Owner routes are local only.

The cloudflared tunnel exists so Twilio can reach the webhooks and the media stream. Anything
else arriving through it (it adds `cf-connecting-ip`) is rejected, so a leaked tunnel URL can't
start paid calls.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

TUNNEL_HEADER = b"cf-connecting-ip"


def is_public_path(path: str) -> bool:
    return path.startswith("/webhooks/") or path == "/media" or path.startswith("/media/")


class LocalOnlyOwnerRoutes:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not is_public_path(scope["path"]):
            if any(name == TUNNEL_HEADER for name, _ in scope.get("headers", [])):
                if scope["type"] == "http":
                    response = JSONResponse({"detail": "Owner routes are local only"}, 403)
                    await response(scope, receive, send)
                else:
                    await WebSocketClose(code=1008)(scope, receive, send)
                return
        await self.app(scope, receive, send)
