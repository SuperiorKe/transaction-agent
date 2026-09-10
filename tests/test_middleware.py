import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.middleware import is_public_path

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


def test_owner_websocket_through_tunnel_is_closed_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/owner-socket", headers=TUNNEL):
            pass
    assert exc_info.value.code == 1008


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
