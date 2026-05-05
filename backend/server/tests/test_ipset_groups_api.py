import pytest

from server.api import ipset_groups as ipset_groups_api
from server.ws.events import EventType, WsEvent


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


@pytest.fixture
def fake_broadcaster(monkeypatch):
    """Перехватывает WS-events чтобы проверить что CRUD их шлёт."""
    events: list[WsEvent] = []

    class _FakeManager:
        async def broadcast(self, *, event):
            events.append(event)

    fake = _FakeManager()
    monkeypatch.setattr(ipset_groups_api, "get_manager", lambda: fake)
    return events


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


async def test_update_group_renames(client, server_id, monkeypatch):
    _patch_agent(monkeypatch, _FakeAgent())
    create = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "old-name", "cidrs": []},
    )
    gid = create.json()["id"]

    response = await client.patch(
        f"/api/v1/servers/{server_id}/ipset-groups/{gid}?apply=false",
        json={"name": "new-name"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "new-name"


async def test_update_group_rename_collision_409(client, server_id, monkeypatch):
    """Переименование в имя другой группы → 409."""
    _patch_agent(monkeypatch, _FakeAgent())
    await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "first", "cidrs": []},
    )
    second = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "second", "cidrs": []},
    )
    second_id = second.json()["id"]

    response = await client.patch(
        f"/api/v1/servers/{server_id}/ipset-groups/{second_id}?apply=false",
        json={"name": "first"},
    )
    assert response.status_code == 409


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


async def test_crud_broadcasts_ws_events(client, server_id, monkeypatch, fake_broadcaster):
    """Регрессия #12: CRUD ipset-group должен слать WS-events чтобы фронт
    инвалидировал список без ручного refetch."""
    _patch_agent(monkeypatch, _FakeAgent())

    create = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "ws-test", "cidrs": []},
    )
    gid = create.json()["id"]

    await client.patch(
        f"/api/v1/servers/{server_id}/ipset-groups/{gid}?apply=false",
        json={"cidrs": ["1.2.3.0/24"]},
    )
    await client.delete(f"/api/v1/servers/{server_id}/ipset-groups/{gid}")

    types = [event.type for event in fake_broadcaster]
    assert EventType.IPSET_GROUP_CREATED in types
    assert EventType.IPSET_GROUP_UPDATED in types
    assert EventType.IPSET_GROUP_DELETED in types
    # Все события привязаны к серверу — фронт фильтрует по server_id.
    assert all(event.server_id == server_id for event in fake_broadcaster)
