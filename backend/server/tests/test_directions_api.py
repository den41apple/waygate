import pytest

from server.api import directions as directions_api
from server.api import ipset_groups as ipset_groups_api
from server.ws.events import EventType, WsEvent


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


class _NoopAgent:
    def __init__(self) -> None:
        self.host = "10.0.0.1"
        self.port = 7743

    async def apply_custom_ipset(self, *, request):
        pass


@pytest.fixture
def fake_broadcaster(monkeypatch):
    events: list[WsEvent] = []

    class _FakeManager:
        async def broadcast(self, *, event):
            events.append(event)

    fake = _FakeManager()
    monkeypatch.setattr(directions_api, "get_manager", lambda: fake)
    return events


async def _seed_geolist(client) -> int:
    response = await client.post(
        "/api/v1/geoip/lists",
        json={"country": "RU", "name": "Russia", "source_url": "https://example.invalid/ru.zone"},
    )
    return int(response.json()["id"])


async def _seed_dns_rule(client, server_id: int) -> tuple[int, str]:
    response = await client.post(
        f"/api/v1/servers/{server_id}/dns",
        json={
            "name": "youtube",
            "domains": ["youtube.com", "*.googlevideo.com"],
            "ipset_name": "dns-youtube",
        },
    )
    return response.json()["id"], "dns-youtube"


async def _seed_ipset_group(client, server_id: int, monkeypatch) -> tuple[int, str]:
    monkeypatch.setattr(ipset_groups_api, "AgentClient", lambda **_kwargs: _NoopAgent())
    response = await client.post(
        f"/api/v1/servers/{server_id}/ipset-groups?apply=false",
        json={"name": "custom-vpn", "cidrs": ["1.2.3.0/24"]},
    )
    return response.json()["id"], "custom-vpn"


async def test_create_direction_materializes_child_rules(
    client,
    server_id,
    monkeypatch,
    fake_broadcaster,
):
    """POST с тремя refs создаёт RoutingDirection + 3 RoutingRule с одним fwmark."""
    geo_id = await _seed_geolist(client)
    dns_id, dns_ipset = await _seed_dns_rule(client, server_id)
    group_id, group_ipset = await _seed_ipset_group(client, server_id, monkeypatch)

    response = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "Streaming-NL",
            "via_interface": "awg-nl",
            "via_gateway": "10.66.66.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [dns_id],
            "ipset_group_ids": [group_id],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Streaming-NL"
    assert body["fwmark"] == 1  # first direction
    assert body["table_id"] == 100
    assert sorted(body["geo_list_ids"]) == [geo_id]
    assert sorted(body["dns_rule_ids"]) == [dns_id]
    assert sorted(body["ipset_group_ids"]) == [group_id]

    # Проверим что в /servers/{id}/rules появились 3 RoutingRule с одним fwmark
    rules_response = await client.get(f"/api/v1/servers/{server_id}/rules")
    rules = rules_response.json()["rules"]
    assert len(rules) == 3
    fwmarks = {rule["fwmark"] for rule in rules}
    assert fwmarks == {1}
    table_ids = {rule["table_id"] for rule in rules}
    assert table_ids == {100}
    via_ifaces = {rule["via_interface"] for rule in rules}
    assert via_ifaces == {"awg-nl"}
    ipset_names = {rule["ipset_name"] for rule in rules}
    assert ipset_names == {"geoip-ru", dns_ipset, group_ipset}

    assert any(event.type is EventType.DIRECTION_CREATED for event in fake_broadcaster)


async def test_create_second_direction_gets_next_fwmark(
    client,
    server_id,
    monkeypatch,
    fake_broadcaster,
):
    geo_id = await _seed_geolist(client)
    # Первое направление
    await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "first",
            "via_interface": "awg-a",
            "via_gateway": "10.0.0.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [],
            "ipset_group_ids": [],
        },
    )
    # Второе
    response = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "second",
            "via_interface": "awg-b",
            "via_gateway": "10.0.0.2",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [],
            "ipset_group_ids": [],
        },
    )
    assert response.status_code == 201
    assert response.json()["fwmark"] == 2
    assert response.json()["table_id"] == 101


async def test_list_directions_returns_refs(client, server_id, monkeypatch, fake_broadcaster):
    geo_id = await _seed_geolist(client)
    dns_id, _ = await _seed_dns_rule(client, server_id)
    await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "test",
            "via_interface": "awg-x",
            "via_gateway": "10.0.0.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [dns_id],
            "ipset_group_ids": [],
        },
    )
    response = await client.get(f"/api/v1/servers/{server_id}/directions")
    assert response.status_code == 200
    directions = response.json()["directions"]
    assert len(directions) == 1
    assert sorted(directions[0]["geo_list_ids"]) == [geo_id]
    assert sorted(directions[0]["dns_rule_ids"]) == [dns_id]


async def test_update_direction_rebuilds_children(
    client,
    server_id,
    monkeypatch,
    fake_broadcaster,
):
    """PATCH с новыми refs должен полностью пересобрать RoutingRule'ы."""
    geo_id = await _seed_geolist(client)
    dns_id, _ = await _seed_dns_rule(client, server_id)

    create = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "x",
            "via_interface": "awg-x",
            "via_gateway": "10.0.0.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [dns_id],
            "ipset_group_ids": [],
        },
    )
    direction_id = create.json()["id"]

    # Убираем dns ref'ы
    response = await client.patch(
        f"/api/v1/servers/{server_id}/directions/{direction_id}",
        json={"dns_rule_ids": []},
    )
    assert response.status_code == 200
    assert response.json()["dns_rule_ids"] == []

    # rules пересобраны: только GeoIP-ipset должен остаться
    rules_response = await client.get(f"/api/v1/servers/{server_id}/rules")
    ipsets = {rule["ipset_name"] for rule in rules_response.json()["rules"]}
    assert "dns-youtube" not in ipsets


async def test_delete_direction_cascades_rules(
    client,
    server_id,
    monkeypatch,
    fake_broadcaster,
):
    geo_id = await _seed_geolist(client)
    create = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "tmp",
            "via_interface": "awg-x",
            "via_gateway": "10.0.0.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [],
            "ipset_group_ids": [],
        },
    )
    direction_id = create.json()["id"]

    rules_before = await client.get(f"/api/v1/servers/{server_id}/rules")
    assert len(rules_before.json()["rules"]) == 1

    response = await client.delete(f"/api/v1/servers/{server_id}/directions/{direction_id}")
    assert response.status_code == 204

    rules_after = await client.get(f"/api/v1/servers/{server_id}/rules")
    assert rules_after.json()["rules"] == []
    assert any(event.type is EventType.DIRECTION_DELETED for event in fake_broadcaster)


async def test_collect_refs_reads_from_direction_sources_pivot(
    client,
    server_id,
    monkeypatch,
    fake_broadcaster,
) -> None:
    """B1: `_collect_refs` читает источники из pivot-таблицы `direction_sources`.

    Раньше (#C5) reverse-lookup парсил `RoutingRule.ipset_name` (legacy
    `geoip-ru-v4` vs modern `geoip-ru`). После B1 источники хранятся явно
    типизировано — никакого string-magic'а. Тест убеждается что
    create-direction → fetch'ит refs обратно через _collect_refs корректно.
    """
    geo_id = await _seed_geolist(client)
    dns_id, _ = await _seed_dns_rule(client, server_id)
    group_id, _ = await _seed_ipset_group(client, server_id, monkeypatch)

    create = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "pivot-test",
            "via_interface": "awg-x",
            "via_gateway": "10.0.0.1",
            "geo_list_ids": [geo_id],
            "dns_rule_ids": [dns_id],
            "ipset_group_ids": [group_id],
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["geo_list_ids"] == [geo_id]
    assert body["dns_rule_ids"] == [dns_id]
    assert body["ipset_group_ids"] == [group_id]

    # GET тоже должен вернуть тот же набор refs (читается через _collect_refs).
    listing = await client.get(f"/api/v1/servers/{server_id}/directions")
    assert listing.status_code == 200
    direction = listing.json()["directions"][0]
    assert sorted(direction["geo_list_ids"]) == [geo_id]
    assert sorted(direction["dns_rule_ids"]) == [dns_id]
    assert sorted(direction["ipset_group_ids"]) == [group_id]


async def test_create_direction_404_when_awg_client_not_found(
    client,
    server_id,
    fake_broadcaster,
):
    response = await client.post(
        f"/api/v1/servers/{server_id}/directions",
        json={
            "name": "bad",
            "awg_client_id": 99999,
            "via_interface": "awg-x",
            "via_gateway": "10.0.0.1",
        },
    )
    assert response.status_code == 400
