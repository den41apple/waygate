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


async def test_update_geo_list_changes_name_and_url(client):
    create = await client.post(
        "/api/v1/geoip/lists",
        json={
            "country": "DE",
            "name": "Germany",
            "source_url": "https://example.com/de.zone",
        },
    )
    list_id = create.json()["id"]

    patch = await client.patch(
        f"/api/v1/geoip/lists/{list_id}",
        json={"name": "Germany (RIPE)", "source_url": "https://example.com/de2.zone"},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["name"] == "Germany (RIPE)"
    assert body["source_url"] == "https://example.com/de2.zone"
    assert body["country"] == "DE"  # country не трогаем


async def test_update_geo_list_404_for_unknown_id(client):
    response = await client.patch("/api/v1/geoip/lists/99999", json={"name": "x"})
    assert response.status_code == 404
