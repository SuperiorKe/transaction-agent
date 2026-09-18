"""Owner routes are local only.

Four independent checks gate every non-webhook path: the cloudflared tunnel exists so the
telephony provider can reach the voice webhooks, so anything else arriving through it (it adds
`cf-connecting-ip`) is rejected; the request's `Host` must be a loopback spelling (a leaked tunnel
URL, or a browser pointed somewhere else, is rejected even without the tunnel header); a POST
carrying an `Origin` header must have a loopback origin too (blocks a foreign page's browser from
using the owner's local API as its call trigger); and the ASGI-reported peer address, when there
is one, must actually be loopback too -- the one signal here a client can't spoof with a header,
so it's what makes "local only" true even if the process is ever bound wider than 127.0.0.1.
`/webhooks/*` is exempt from all four -- it's the one path the tunnel and Twilio are meant to
reach.
"""

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

TUNNEL_HEADER = b"cf-connecting-ip"
HOST_HEADER = b"host"
ORIGIN_HEADER = b"origin"
# HTTP methods a browser form or fetch() can use to change state. GET/HEAD/OPTIONS are read-only
# and, unlike POST, are also how a plain <img>/<link> cross-origin request looks -- checking Origin
# on them would reject ordinary cross-origin reads that were never a CSRF-style risk here.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _header(scope: Scope, name: bytes) -> str | None:
    for header_name, value in scope.get("headers", []):
        if header_name.lower() == name:
            return value.decode("latin-1")
    return None


def _is_loopback_host(host: str, *, allow_testserver: bool = False) -> bool:
    """Accept only the browser-visible spellings of the loopback interface.

    This deliberately doesn't resolve names: a hostname which happens to resolve to 127.0.0.1 is
    still unsafe for owner routes because it can be supplied by an attacker during DNS rebinding.
    """
    host = host.lower()
    allowed = {"localhost", "127.0.0.1", "[::1]"}
    if allow_testserver:
        allowed.add("testserver")  # Starlette's in-process TestClient default Host.
    if host in allowed:
        return True
    for candidate in allowed:
        if host.startswith(f"{candidate}:"):
            port = host[len(candidate) + 1 :]
            # str.isdigit() is true for non-ASCII digit characters (e.g. superscript two); no real
            # HTTP client sends those in a port, so require plain ASCII digits.
            if port.isascii() and port.isdigit():
                return True
    return False


def _is_loopback_peer(client: tuple[str, int] | None, *, allow_testserver: bool) -> bool:
    """The ASGI-reported real peer address, when there is one.

    Host/Origin/cf-connecting-ip are all client-supplied headers -- trustworthy today only because
    uvicorn's default bind is 127.0.0.1, not because this middleware enforces it. This is the one
    signal a client can't spoof by sending a different header: if the process is ever started with
    `--host 0.0.0.0` (a plausible one-flag mistake when demoing over Wi-Fi), a LAN peer sending
    `Host: localhost` would otherwise still pass. `client` is None for a transport with no peer
    tuple (e.g. a Unix socket) -- nothing to check there, so it passes; the header checks still
    apply regardless.
    """
    if client is None:
        return True
    return client[0] in {"127.0.0.1", "::1"} or (allow_testserver and client[0] == "testclient")


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    # urlsplit's hostname removes IPv6 brackets; reject paths, queries and fragments too.
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return False
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def is_public_path(path: str) -> bool:
    return path.startswith("/webhooks/")


class LocalOnlyOwnerRoutes:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not is_public_path(scope["path"]):
            host = _header(scope, HOST_HEADER)
            origin = _header(scope, ORIGIN_HEADER)
            tunneled = any(name.lower() == TUNNEL_HEADER for name, _ in scope.get("headers", []))
            # ASGI makes `client` optional: it can be absent OR present-and-None (uvicorn does the
            # latter for a Unix-socket peer), so never subscript it without the `or` fallback.
            client = scope.get("client")
            test_client = (client or (None, None))[0] == "testclient"
            forbidden = (
                tunneled
                or host is None
                or not _is_loopback_host(host, allow_testserver=test_client)
                or not _is_loopback_peer(client, allow_testserver=test_client)
            )
            # Curl and server-to-server local clients do not send Origin. A browser does, and a
            # foreign page must not be able to use a loopback service as its call trigger. A
            # WebSocket handshake always carries Origin from a browser (unlike a plain HTTP GET),
            # so unlike the HTTP branch below, its absence is itself forbidden rather than ignored.
            if scope["type"] == "websocket":
                forbidden = forbidden or origin is None or not _is_loopback_origin(origin)
            elif (
                scope["type"] == "http"
                and (scope.get("method") or "").upper() in MUTATING_METHODS
                and origin is not None
            ):
                forbidden = forbidden or not _is_loopback_origin(origin)
            if forbidden:
                if scope["type"] == "http":
                    response = JSONResponse({"detail": "Owner routes are local only"}, 403)
                    await response(scope, receive, send)
                else:
                    await WebSocketClose(code=1008)(scope, receive, send)
                return
        await self.app(scope, receive, send)
