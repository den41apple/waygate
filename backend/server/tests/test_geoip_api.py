async def test_geoip_list_crud(client):
    response = await client.post(
        "/api/v1/geoip/lists",
        json={
            "country": "RU",
            "name": "Russia (ipdeny)",
            "source_url": "https://ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone",
        },
    )
    assert response.status_code == 201
    list_id = response.json()["id"]
    assert response.json()["status"] == "stale"

    response = await client.get("/api/v1/geoip/lists")
    assert response.status_code == 200
    assert len(response.json()["lists"]) == 1

    response = await client.delete(f"/api/v1/geoip/lists/{list_id}")
    assert response.status_code == 204

    response = await client.get("/api/v1/geoip/lists")
    assert response.json()["lists"] == []
