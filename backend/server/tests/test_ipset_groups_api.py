import pytest

from server.api import ipset_groups as ipset_groups_api


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


class _FakeAgent:
    def __init__(self) -> None:
        self.applied: list[tuple[str, list[str]]] = []
        self.host = "10.0.0.1"
        self.port = 7743

    async def apply_custom_ipset(self, *, request):
        self.applied.append((request.name, list(request.cidrs)))


def _patch_agent(monkeypatch, fake: _FakeAgent) -> None:
    monkeypatch.setattr(ipset_groups_api, "AgentClient", lambda **_kwargs: fake)


async def test_create_ipset_group_persists_and_applies(client, server_id, monkeypatch):
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)

    response = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups",
        json={"name": "custom-vpn", "cidrs": ["1.2.3.0/24", "10.0.0.0/8"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "custom-vpn"
    assert body["cidrs"] == ["1.2.3.0/24", "10.0.0.0/8"]
    # Должен был применить на агенте по умолчанию (apply=true)
    assert fake.applied == [("custom-vpn", ["1.2.3.0/24", "10.0.0.0/8"])]


async def test_create_ipset_group_without_apply(client, server_id, monkeypatch):
    """`?apply=false` — записать в БД, на агента не отправлять."""
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)

    response = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "draft", "cidrs": ["192.168.1.0/24"]},
    )
    assert response.status_code == 201
    assert fake.applied == []


async def test_list_groups(client, server_id, monkeypatch):
    _patch_agent(monkeypatch, _FakeAgent())
    await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups",
        json={"name": "a", "cidrs": ["1.1.1.0/24"]},
    )
    await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups",
        json={"name": "b", "cidrs": ["2.2.2.0/24"]},
    )
    response = await client.get(f"/api/v1/servers/{server_id}/ipset-groups")
    assert response.status_code == 200
    names = [group["name"] for group in response.json()["groups"]]
    assert sorted(names) == ["a", "b"]


async def test_update_group_applies(client, server_id, monkeypatch):
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)

    create = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "myset", "cidrs": ["1.1.1.0/24"]},
    )
    gid = create.json()["id"]

    response = await client.patch(
        f"/api/v1/servers/{server_id}/ipset-groups/{gid}",
        json={"cidrs": ["1.1.1.0/24", "5.6.7.0/24"]},
    )
    assert response.status_code == 200
    assert response.json()["cidrs"] == ["1.1.1.0/24", "5.6.7.0/24"]
    assert fake.applied == [("myset", ["1.1.1.0/24", "5.6.7.0/24"])]


async def test_delete_group(client, server_id, monkeypatch):
    _patch_agent(monkeypatch, _FakeAgent())
    create = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "tmp", "cidrs": []},
    )
    gid = create.json()["id"]
    response = await client.delete(f"/api/v1/servers/{server_id}/ipset-groups/{gid}")
    assert response.status_code == 204
    listing = await client.get(f"/api/v1/servers/{server_id}/ipset-groups")
    assert listing.json()["groups"] == []
