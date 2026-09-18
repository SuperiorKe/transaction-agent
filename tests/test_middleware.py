import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.middleware import LocalOnlyOwnerRoutes, _is_loopback_host, is_public_path

TUNNEL = {"cf-connecting-ip": "203.0.113.7"}


@pytest.fixture
def client() -> TestClient:
    # Not used as a context manager, so the lifespan (init_db on the real DB file) never runs.
    return TestClient(create_app())


def test_owner_route_through_tunnel_is_forbidden(client):
    response = client.post("/transactions", headers=TUNNEL)
    assert response.status_code == 403
    assert response.json() == {"detail": "Owner routes are local only"}


def test_health_through_tunnel_is_forbidden(client):
    assert client.get("/health", headers=TUNNEL).status_code == 403


def test_webhook_through_tunnel_passes_middleware(client):
    # No webhook route exists until #2; a 404 means the request reached the router.
    assert client.post("/webhooks/voice", headers=TUNNEL).status_code == 404


def test_local_request_is_allowed(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("host", ["example.com", "example.com:8000", "127.0.0.2", "[::2]"])
def test_owner_route_with_a_foreign_host_is_forbidden(client, host):
    response = client.get("/health", headers={"host": host})
    assert response.status_code == 403
    assert response.json() == {"detail": "Owner routes are local only"}


@pytest.mark.parametrize("host", ["localhost", "localhost:5173", "127.0.0.1:8000", "[::1]:8000"])
def test_owner_route_with_a_loopback_host_is_allowed(client, host):
    assert client.get("/health", headers={"host": host}).status_code == 200


async def test_owner_route_without_a_host_header_is_forbidden():
    """httpx always sends Host, so this drives the middleware's ASGI interface directly. A request
    with no Host can't be shown to be loopback, so it is refused rather than trusted."""
    sent: list[dict] = []

    async def never_called(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("the middleware let a hostless owner request through")

    async def send(message):
        sent.append(message)

    # `client: None` is ASGI-legal (uvicorn does it for a Unix-socket peer), so the middleware
    # must read it defensively rather than subscripting it.
    scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "client": None}
    await LocalOnlyOwnerRoutes(never_called)(scope, None, send)

    assert sent[0]["status"] == 403


@pytest.mark.parametrize("host", ["localhost:", "localhost:notaport", "127.0.0.1:80x"])
def test_owner_route_with_a_malformed_loopback_port_is_forbidden(client, host):
    assert client.get("/health", headers={"host": host}).status_code == 403


def test_is_loopback_host_rejects_a_non_ascii_digit_port():
    # str.isdigit() is true for non-ASCII digit characters (e.g. superscript two, '\xb2'); no real
    # HTTP client can even send one as a Host header (httpx itself refuses to encode it), but the
    # parser should not be looser than every real client. Unit-level: httpx's ASCII-only header
    # encoding means this can't be driven through the TestClient at the request level.
    assert _is_loopback_host("localhost:²") is False


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost.attacker.example",  # loopback name only as a subdomain prefix
        "http://user@localhost",  # credentials in the origin
        "http://localhost/path",  # an origin is never a path
        "null",  # a sandboxed iframe's opaque origin
    ],
)
def test_owner_post_with_a_non_loopback_origin_spelling_is_forbidden(client, origin):
    assert client.post("/transactions", headers={"origin": origin}).status_code == 403


def test_owner_get_is_not_origin_checked(client):
    """Only POST carries the Origin check: a cross-origin GET can't read the reply (no CORS
    headers) and can't start a paid call, and curl/server-to-server GETs send no Origin."""
    assert client.get("/health", headers={"origin": "https://attacker.example"}).status_code == 200


def test_owner_post_with_a_foreign_origin_is_forbidden(client):
    response = client.post("/transactions", headers={"origin": "https://attacker.example"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Owner routes are local only"}


@pytest.mark.parametrize(
    "origin", ["http://localhost:5173", "https://127.0.0.1:8443", "http://[::1]"]
)
def test_owner_post_with_a_loopback_origin_passes_middleware(client, origin):
    # It reaches FastAPI's request validation (rather than the middleware); the body is invalid.
    assert client.post("/transactions", headers={"origin": origin}).status_code == 422


@pytest.mark.parametrize("origin", ["http://[::1", "http://[v1.fe80::a"])
def test_owner_post_with_a_malformed_origin_is_forbidden(client, origin):
    # urlsplit() raises ValueError on an unclosed IPv6 bracket; that must fail closed, not 500.
    assert client.post("/transactions", headers={"origin": origin}).status_code == 403


def test_owner_websocket_through_tunnel_is_closed_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/owner-socket", headers=TUNNEL):
            pass
    assert exc_info.value.code == 1008


def test_owner_websocket_with_a_foreign_host_is_closed_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/owner-socket", headers={"host": "attacker.example"}):
            pass
    assert exc_info.value.code == 1008


def test_owner_websocket_with_no_origin_is_closed_with_1008(client):
    # A browser always sends Origin on a WebSocket handshake (unlike a plain HTTP GET), so unlike
    # the HTTP branch, a websocket with a passing Host and no Origin at all must still be rejected.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/owner-socket"):
            pass
    assert exc_info.value.code == 1008


def test_owner_websocket_with_a_foreign_origin_is_closed_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/owner-socket", headers={"origin": "https://attacker.example"}
        ):
            pass
    assert exc_info.value.code == 1008


def test_webhook_is_exempt_from_host_and_origin_checks(client):
    assert (
        client.post(
            "/webhooks/voice",
            headers={"host": "provider.example", "origin": "https://provider.example"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("path", "public"),
    [
        ("/webhooks/voice", True),
        ("/webhooks/voice/some-secret", True),
        ("/media", False),
        ("/webhooks", False),
        ("/transactions", False),
        ("/", False),
    ],
)
def test_is_public_path(path, public):
    assert is_public_path(path) is public
