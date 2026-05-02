import pytest

from server.api import servers as servers_api
from server.ws.events import EventType, WsEvent
from shared.schemas import AgentStatus, UpdateResponse, UpdateStatus


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


async def test_update_calls_agent_and_waits_for_reconnect(client, server_id, monkeypatch):
    captured: dict = {}
    broadcasts: list[WsEvent] = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def update(self, *, request):
            captured["update_request"] = request
            return UpdateResponse(previous_version="0.1.0", status=UpdateStatus.RESTARTING)

        async def status(self):
            return AgentStatus(
                version="0.2.0",
                uptime_seconds=1,
                hostname="edge",
                awg_containers=[],
                rules_applied=0,
                tls_mode=None,
            )

    class FakeManager:
        async def broadcast(self, *, event):
            broadcasts.append(event)

    fake_manager = FakeManager()

    monkeypatch.setattr(servers_api, "AgentClient", FakeClient)
    monkeypatch.setattr(servers_api, "get_manager", lambda: fake_manager)

    response = await client.post(
        f"/api/v1/servers/{server_id}/update",
        json={"version": "0.2.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["previous_version"] == "0.1.0"
    assert body["status"] == "restarting"

    assert captured["update_request"].version == "0.2.0"
    assert any(event.type is EventType.SERVER_AGENT_UPDATED for event in broadcasts)

    response = await client.get(f"/api/v1/servers/{server_id}")
    assert response.json()["version"] == "0.2.0"


async def test_update_returns_502_when_agent_unreachable(client, server_id, monkeypatch):
    from server.agent_client import AgentUnreachable

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def update(self, *, request):
            raise AgentUnreachable("connection refused")

    monkeypatch.setattr(servers_api, "AgentClient", FakeClient)

    response = await client.post(
        f"/api/v1/servers/{server_id}/update",
        json={"version": "0.2.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 502
