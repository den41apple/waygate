"""Тесты server-side proxy для GET /api/v1/servers/{id}/containers."""

import pytest

from server.api import containers as containers_api
from shared.schemas import AwgClientStatus, ContainerInfo, ContainerListResponse


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


class _FakeAgent:
    def __init__(self, *, response: ContainerListResponse) -> None:
        self._response = response
        self.host = "10.0.0.1"
        self.port = 7743

    async def list_containers(self) -> ContainerListResponse:
        return self._response


async def test_list_containers_proxies_to_agent(client, server_id, monkeypatch):
    fake = _FakeAgent(
        response=ContainerListResponse(
            containers=[
                ContainerInfo(
                    name="waygate-amnezia-client-firstbyte",
                    status=AwgClientStatus.RUNNING,
                    image="waygate-awg-client:latest",
                    is_waygate_managed=True,
                ),
                ContainerInfo(
                    name="my-awg-server",
                    status=AwgClientStatus.RUNNING,
                    image="amneziavpn/amnezia-server:latest",
                    is_waygate_managed=False,
                ),
            ],
        ),
    )
    monkeypatch.setattr(containers_api, "AgentClient", lambda **_kwargs: fake)

    response = await client.get(f"/api/v1/servers/{server_id}/containers")
    assert response.status_code == 200
    body = response.json()
    names = [c["name"] for c in body["containers"]]
    assert names == ["waygate-amnezia-client-firstbyte", "my-awg-server"]
    managed = {c["name"]: c["is_waygate_managed"] for c in body["containers"]}
    assert managed["waygate-amnezia-client-firstbyte"] is True
    assert managed["my-awg-server"] is False


async def test_list_containers_404_for_unknown_server(client):
    response = await client.get("/api/v1/servers/999/containers")
    assert response.status_code == 404
