import base64
from datetime import UTC, datetime, timedelta

import pytest

from server.api import tls as tls_api
from server.ws.events import EventType, WsEvent
from shared.schemas import TlsApplyResponse


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


async def test_apply_tls_calls_agent_and_persists(client, server_id, monkeypatch):
    captured: dict = {}
    broadcasts: list[WsEvent] = []
    expires_at = datetime.now(tz=UTC) + timedelta(days=90)

    class FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def apply_tls(self, *, config):
            captured["config"] = config
            return TlsApplyResponse(
                cert_path="/etc/waygate/tls/cert.pem",
                expires_at=expires_at,
                domains=["edge.example.com"],
            )

    class FakeManager:
        async def broadcast(self, *, event):
            broadcasts.append(event)

    fake_manager = FakeManager()

    monkeypatch.setattr(tls_api, "AgentClient", FakeClient)
    monkeypatch.setattr(tls_api, "get_manager", lambda: fake_manager)

    response = await client.post(
        f"/api/v1/servers/{server_id}/tls",
        json={
            "mode": "upload",
            "port": 7743,
            "cert_pem": base64.b64encode(b"PEM-CERT").decode("ascii"),
            "key_pem": base64.b64encode(b"PEM-KEY").decode("ascii"),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["domains"] == ["edge.example.com"]

    # GET возвращает persist'ed config с redacted secrets
    response = await client.get(f"/api/v1/servers/{server_id}/tls")
    assert response.status_code == 200
    persisted = response.json()
    assert persisted["config"]["mode"] == "upload"
    assert persisted["config"]["cert_pem"] == "***"
    assert persisted["config"]["key_pem"] == "***"

    assert len(broadcasts) == 1
    assert broadcasts[0].type is EventType.TLS_APPLIED
    assert broadcasts[0].payload["mode"] == "upload"


async def test_get_tls_404_when_not_configured(client, server_id):
    response = await client.get(f"/api/v1/servers/{server_id}/tls")
    assert response.status_code == 404
