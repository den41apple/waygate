import pytest

from server.api import rules as rules_api
from server.ws.events import EventType, WsEvent
from shared.schemas import ApplyRulesResponse


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


async def test_rule_crud(client, server_id):
    create_payload = {
        "country": "RU",
        "ipset_name": "russia",
        "fwmark": 256,
        "table_id": 100,
        "via_interface": "awg0",
        "via_gateway": "10.0.0.1",
    }
    response = await client.post(f"/api/v1/servers/{server_id}/rules", json=create_payload)
    assert response.status_code == 201
    rule_id = response.json()["id"]
    assert response.json()["enabled"] is True

    response = await client.get(f"/api/v1/servers/{server_id}/rules")
    assert len(response.json()["rules"]) == 1

    response = await client.patch(
        f"/api/v1/servers/{server_id}/rules/{rule_id}",
        json={"enabled": False, "via_gateway": "10.0.0.2"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["via_gateway"] == "10.0.0.2"

    response = await client.delete(f"/api/v1/servers/{server_id}/rules/{rule_id}")
    assert response.status_code == 204

    response = await client.get(f"/api/v1/servers/{server_id}/rules")
    assert response.json()["rules"] == []


async def test_apply_rules_calls_agent(client, server_id, monkeypatch):
    captured: dict = {}
    broadcasts: list[WsEvent] = []

    class FakeClient:
        def __init__(self, *, host, port, token):
            captured["host"] = host
            captured["port"] = port
            captured["token"] = token

        async def apply_rules(self, *, request):
            captured["rules"] = request.rules
            return ApplyRulesResponse(applied=len(request.rules), skipped=0, errors=[])

    class FakeManager:
        async def broadcast(self, *, event):
            broadcasts.append(event)

    fake_manager = FakeManager()

    def fake_get_manager():
        return fake_manager

    monkeypatch.setattr(rules_api, "AgentClient", FakeClient)
    monkeypatch.setattr(rules_api, "get_manager", fake_get_manager)

    await client.post(
        f"/api/v1/servers/{server_id}/rules",
        json={
            "country": "RU",
            "ipset_name": "russia",
            "fwmark": 256,
            "table_id": 100,
            "via_interface": "awg0",
            "via_gateway": "10.0.0.1",
        },
    )

    response = await client.post(f"/api/v1/servers/{server_id}/rules/apply")
    assert response.status_code == 200
    assert response.json()["applied"] == 1
    assert captured["host"] == "10.0.0.1"
    assert len(captured["rules"]) == 1
    assert captured["rules"][0].country == "RU"
    assert len(broadcasts) == 1
    assert broadcasts[0].type is EventType.RULE_APPLIED
    assert broadcasts[0].server_id == server_id
    assert broadcasts[0].payload["applied"] == 1
