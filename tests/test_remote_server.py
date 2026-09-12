"""Testes do servidor remoto somente-leitura (src/server/remote_server.py):
sobe um servidor real em 127.0.0.1 com porta efêmera (port=0) e bate nos
endpoints via httpx, validando autenticação por token e os dados servidos."""
import httpx
import pytest

from src.server.remote_server import RemoteStatusServer


@pytest.fixture
def server():
    srv = RemoteStatusServer(
        host="127.0.0.1", port=0, token="segredo123",
        status_provider=lambda: {
            "cameras": [{"name": "Sala", "connected": True, "fps": 12.3}],
            "whatsapp_configured": True, "voice_available": False,
        },
        history_provider=lambda limit: [{"event_id": "EVT-01", "severity": "CRITICAL"}][:limit],
        snapshot_fps=100.0,  # alto o suficiente para nao ser throttlado durante os testes
    )
    srv.start()
    yield srv
    srv.stop()


def url(server, path):
    return f"http://{server.host}:{server.port}{path}"


def test_status_requires_token(server):
    response = httpx.get(url(server, "/api/status"))
    assert response.status_code == 401


def test_status_with_token_as_query_param(server):
    response = httpx.get(url(server, "/api/status"), params={"token": "segredo123"})
    assert response.status_code == 200
    assert response.json()["cameras"][0]["name"] == "Sala"


def test_status_with_token_as_bearer_header(server):
    response = httpx.get(url(server, "/api/status"), headers={"Authorization": "Bearer segredo123"})
    assert response.status_code == 200


def test_status_with_wrong_token_rejected(server):
    response = httpx.get(url(server, "/api/status"), params={"token": "errado"})
    assert response.status_code == 401


def test_history_respects_limit(server):
    response = httpx.get(url(server, "/api/history"), params={"token": "segredo123", "limit": 1})
    assert response.status_code == 200
    assert len(response.json()["events"]) == 1


def test_snapshot_not_found_before_any_frame(server):
    response = httpx.get(url(server, "/api/snapshot/Sala"), params={"token": "segredo123"})
    assert response.status_code == 404


def test_snapshot_served_after_set_snapshot(server):
    server.set_snapshot("Sala", b"\xff\xd8fake-jpeg-bytes")

    response = httpx.get(url(server, "/api/snapshot/Sala"), params={"token": "segredo123"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8fake-jpeg-bytes"


def test_dashboard_root_served_without_token(server):
    response = httpx.get(url(server, "/"))
    assert response.status_code == 200
    assert "Kinesis" in response.text


def test_health_endpoint_without_token(server):
    response = httpx.get(url(server, "/api/health"))
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_unknown_route_returns_404(server):
    response = httpx.get(url(server, "/api/rota-desconhecida"), params={"token": "segredo123"})
    assert response.status_code == 404


def test_constructor_rejects_empty_token():
    with pytest.raises(ValueError):
        RemoteStatusServer(
            host="127.0.0.1", port=0, token="",
            status_provider=lambda: {}, history_provider=lambda limit: [],
        )


def test_should_capture_snapshot_throttles_independently_per_camera():
    srv = RemoteStatusServer(
        host="127.0.0.1", port=0, token="x",
        status_provider=lambda: {}, history_provider=lambda limit: [],
        snapshot_fps=1.0,
    )

    assert srv.should_capture_snapshot("Sala", now=10.0) is True
    assert srv.should_capture_snapshot("Sala", now=10.1) is False
    assert srv.should_capture_snapshot("Cozinha", now=10.1) is True
    assert srv.should_capture_snapshot("Sala", now=11.1) is True
