import pytest


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


async def test_dns_crud(client, server_id):
    response = await client.post(
        f"/api/v1/servers/{server_id}/dns",
        json={"name": "tracker", "domains": ["bt.example.com"], "ipset_name": "tracker"},
    )
    assert response.status_code == 201
    rule_id = response.json()["id"]
    assert response.json()["domains"] == ["bt.example.com"]

    response = await client.get(f"/api/v1/servers/{server_id}/dns")
    assert len(response.json()["rules"]) == 1

    response = await client.patch(
        f"/api/v1/servers/{server_id}/dns/{rule_id}",
        json={"domains": ["bt.example.com", "torrent.example.com"]},
    )
    assert response.status_code == 200
    assert len(response.json()["domains"]) == 2

    response = await client.delete(f"/api/v1/servers/{server_id}/dns/{rule_id}")
    assert response.status_code == 204
