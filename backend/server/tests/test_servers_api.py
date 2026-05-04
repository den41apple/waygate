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


async def test_delete_server_handles_audit_entries(client, session_maker):
    """Регрессия на FK violation: `audit_entries.server_id` ссылается на
    `server.id`. До фикса DELETE падал с IntegrityError на проде, так как
    очищались только rules/dns/metrics/tls, а audit оставался висеть. Аудит —
    историческая запись, удалять её жалко, но FK обнуляется."""
    from server.models import AuditEntry

    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.42", "port": 7743, "name": "edge-de", "token": "tok-de"},
    )
    server_id = response.json()["id"]

    # Имитируем audit-middleware (он в тестах не активен — пишет в production-engine).
    async with session_maker() as session:
        session.add(
            AuditEntry(
                method="POST",
                path=f"/api/v1/servers/{server_id}/rules",
                server_id=server_id,
                status_code=201,
                user="test-admin",
                ip="127.0.0.1",
                payload={"country": "DE"},
            ),
        )
        await session.commit()

    response = await client.delete(f"/api/v1/servers/{server_id}")
    assert response.status_code == 204, response.text

    # Сервер удалён, audit остался — но server_id обнулён.
    async with session_maker() as session:
        from sqlalchemy import select as sa_select

        result = await session.execute(sa_select(AuditEntry))
        entries = result.scalars().all()
        assert len(entries) == 1
        assert entries[0].server_id is None
        assert entries[0].path == f"/api/v1/servers/{server_id}/rules"


async def test_update_server_name_and_region(client):
    create = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.5", "port": 7743, "name": "old-name", "token": "tok-x"},
    )
    server_id = create.json()["id"]

    patch = await client.patch(
        f"/api/v1/servers/{server_id}",
        json={"name": "new-name", "region": "APAC"},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["name"] == "new-name"
    assert body["region"] == "APAC"
    # host/port/token PATCH не трогает
    assert body["host"] == "10.0.0.5"
    assert body["port"] == 7743


async def test_update_unknown_server_404(client):
    response = await client.patch("/api/v1/servers/99999", json={"name": "x"})
    assert response.status_code == 404


async def test_save_ssh_credentials_via_patch(client, session_maker):
    """Plaintext password принимается, шифруется и сохраняется. GET возвращает только has_ssh_password."""
    from server.auth.secrets import decrypt
    from server.models import Server

    create = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.6", "port": 7743, "name": "ssh-test", "token": "tok"},
    )
    server_id = create.json()["id"]
    assert create.json()["has_ssh_password"] is False
    assert create.json()["has_ssh_private_key"] is False

    patch = await client.patch(
        f"/api/v1/servers/{server_id}",
        json={"ssh_password": "secret-pass-123", "ssh_user": "admin", "ssh_port": 2222},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["has_ssh_password"] is True
    assert body["ssh_user"] == "admin"
    assert body["ssh_port"] == 2222
    # Plaintext в response никогда не возвращается.
    assert "ssh_password" not in body
    assert "ssh_private_key" not in body

    # В БД лежит зашифрованный токен; decrypt возвращает оригинал.
    async with session_maker() as session:
        server = await session.get(Server, server_id)
        assert server is not None
        assert server.ssh_password_encrypted is not None
        assert server.ssh_password_encrypted != "secret-pass-123"  # зашифрованный != plaintext
        assert decrypt(token=server.ssh_password_encrypted) == "secret-pass-123"


async def test_clear_ssh_credentials_via_empty_string(client):
    """PATCH с `""` для ssh_password — удаляет cred (=> null в БД)."""
    create = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.7", "port": 7743, "name": "ssh-clear", "token": "tok"},
    )
    server_id = create.json()["id"]

    # Сначала ставим
    await client.patch(f"/api/v1/servers/{server_id}", json={"ssh_password": "p"})
    body = (await client.get(f"/api/v1/servers/{server_id}")).json()
    assert body["has_ssh_password"] is True

    # Потом удаляем
    await client.patch(f"/api/v1/servers/{server_id}", json={"ssh_password": ""})
    body = (await client.get(f"/api/v1/servers/{server_id}")).json()
    assert body["has_ssh_password"] is False


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
