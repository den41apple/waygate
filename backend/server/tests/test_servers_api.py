async def test_create_and_list_server(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge-eu", "token": "tok-123", "region": "EU"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["host"] == "10.0.0.1"
    assert body["region"] == "EU"
    assert body["status"] == "offline"
    server_id = body["id"]

    response = await client.get("/api/v1/servers")
    assert response.status_code == 200
    assert len(response.json()["servers"]) == 1

    response = await client.get(f"/api/v1/servers/{server_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "edge-eu"


async def test_get_unknown_server_404(client):
    response = await client.get("/api/v1/servers/9999")
    assert response.status_code == 404


async def test_delete_server_cascades(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.2", "port": 7743, "name": "edge-us", "token": "tok-456"},
    )
    server_id = response.json()["id"]

    await client.post(
        f"/api/v1/servers/{server_id}/rules",
        json={
            "country": "US",
            "ipset_name": "us",
            "fwmark": 100,
            "table_id": 100,
            "via_interface": "awg0",
            "via_gateway": "10.0.0.1",
        },
    )

    response = await client.delete(f"/api/v1/servers/{server_id}")
    assert response.status_code == 204

    response = await client.get(f"/api/v1/servers/{server_id}/rules")
    assert response.status_code == 404
