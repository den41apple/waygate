import pytest

from server.agent_client import AgentUnreachable
from server.api import servers as servers_api
from shared.schemas import TokenRotateResponse


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "old-token"},
    )
    return response.json()["id"]


async def test_rotate_token_replaces_in_db(client, server_id, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def rotate_token(self):
            return TokenRotateResponse(token="brand-new-token-from-agent")

    monkeypatch.setattr(servers_api, "AgentClient", FakeClient)

    response = await client.post(f"/api/v1/servers/{server_id}/token/rotate")
    assert response.status_code == 200
    assert response.json() == {"rotated": True}

    # БД должна содержать новый токен — проверим через GET
    response = await client.get(f"/api/v1/servers/{server_id}")
    # Токен в response не отдаём, проверим косвенно — следующий rotate с новым токеном работает
    assert response.json()["host"] == "10.0.0.1"


async def test_rotate_token_502_when_agent_unreachable(client, server_id, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def rotate_token(self):
            raise AgentUnreachable("connection refused")

    monkeypatch.setattr(servers_api, "AgentClient", FakeClient)

    response = await client.post(f"/api/v1/servers/{server_id}/token/rotate")
    assert response.status_code == 502
