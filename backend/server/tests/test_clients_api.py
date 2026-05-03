import pytest

from server.api import clients as clients_api
from server.auth.secrets import _cipher, decrypt
from server.ws.events import EventType, WsEvent
from shared.schemas import (
    AwgClientActionResponse,
    AwgClientInfo,
    AwgClientStatus,
    CreateAwgClientResponse,
    ListAwgClientsResponse,
)

# Валидные base64-32-байтные ключи для fixture-конфигов.
_PRIV = "Wyw1Tr4L/NV0SKMDjNtwhAKgQQkY/NlMXhwRjZrVQ4o="
_PUB = "k6E1U4ZvkV8Lxay5d8HvPCtHsO0XG6iZzQOvmW+qWrY="

_VALID_CONFIG = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_PRIV}

[Peer]
PublicKey = {_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""


@pytest.fixture(autouse=True)
def _clear_cipher_cache():
    _cipher.cache_clear()
    yield
    _cipher.cache_clear()


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


class _FakeAgent:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.fail_create = fail_create
        self.host = "10.0.0.1"
        self.port = 7743

    async def create_client(self, *, request):
        if self.fail_create:
            from server.agent_client import AgentClientError

            raise AgentClientError("forced agent error")
        self.created.append(request.name)
        return CreateAwgClientResponse(
            client=AwgClientInfo(
                name=request.name,
                container_name=f"waygate-amnezia-client-{request.name}",
                status=AwgClientStatus.RUNNING,
                peer_endpoint="vpn.example.com:51820",
                peer_pubkey=_PUB,
                interface_address="10.66.66.2/24",
            ),
        )

    async def list_clients(self):
        return ListAwgClientsResponse(clients=[])

    async def start_client(self, *, name):
        self.started.append(name)
        return AwgClientActionResponse(name=name, status=AwgClientStatus.RUNNING)

    async def stop_client(self, *, name):
        self.stopped.append(name)
        return AwgClientActionResponse(name=name, status=AwgClientStatus.STOPPED)

    async def delete_client(self, *, name):
        self.deleted.append(name)


def _patch_agent_client(monkeypatch, fake: _FakeAgent) -> None:
    monkeypatch.setattr(clients_api, "AgentClient", lambda **_kwargs: fake)


@pytest.fixture
def fake_broadcaster(monkeypatch):
    """Перехватываем WS broadcast чтобы тест видел эмиттенные события."""
    events: list[WsEvent] = []

    class _FakeManager:
        async def broadcast(self, *, event):
            events.append(event)

    fake = _FakeManager()
    monkeypatch.setattr(clients_api, "get_manager", lambda: fake)
    return events


async def test_create_client_encrypts_config_in_db(client, server_id, monkeypatch, fake_broadcaster, session_maker):
    fake = _FakeAgent()
    _patch_agent_client(monkeypatch, fake)

    response = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "country": "US", "config_text": _VALID_CONFIG},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "us-fast"
    assert body["status"] == AwgClientStatus.RUNNING.value
    assert body["peer_endpoint"] == "vpn.example.com:51820"
    assert body["peer_pubkey"] == _PUB

    # Конфиг в БД зашифрован, не plaintext
    from sqlalchemy import select as sa_select

    from server.models import AwgClient

    async with session_maker() as session:
        result = await session.execute(sa_select(AwgClient))
        record = result.scalars().one()
        assert record.config_encrypted != _VALID_CONFIG
        assert _PRIV not in record.config_encrypted
        assert decrypt(token=record.config_encrypted) == _VALID_CONFIG

    # Агент дёрнут с правильным name
    assert fake.created == ["us-fast"]
    # WS-event эмитнут
    assert any(event.type is EventType.AWG_CLIENT_CREATED for event in fake_broadcaster)


async def test_create_client_rejects_invalid_config(client, server_id, monkeypatch, fake_broadcaster):
    _patch_agent_client(monkeypatch, _FakeAgent())
    response = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "bad", "config_text": "[Interface]\nAddress = ..."},
    )
    assert response.status_code == 400
    assert "невалидный" in response.json()["detail"].lower() or ".conf" in response.json()["detail"]


async def test_create_client_502_when_agent_fails(client, server_id, monkeypatch, fake_broadcaster):
    _patch_agent_client(monkeypatch, _FakeAgent(fail_create=True))
    response = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "config_text": _VALID_CONFIG},
    )
    assert response.status_code == 502


async def test_list_clients_returns_db_records(client, server_id, monkeypatch, fake_broadcaster):
    _patch_agent_client(monkeypatch, _FakeAgent())

    await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "config_text": _VALID_CONFIG},
    )
    await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "de-streaming", "config_text": _VALID_CONFIG},
    )

    response = await client.get(f"/api/v1/servers/{server_id}/clients")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()["clients"]]
    assert sorted(names) == ["de-streaming", "us-fast"]


async def test_delete_client_removes_record_and_calls_agent(client, server_id, monkeypatch, fake_broadcaster):
    fake = _FakeAgent()
    _patch_agent_client(monkeypatch, fake)

    create = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "config_text": _VALID_CONFIG},
    )
    cid = create.json()["id"]

    response = await client.delete(f"/api/v1/servers/{server_id}/clients/{cid}")
    assert response.status_code == 204
    assert fake.deleted == ["us-fast"]

    listing = await client.get(f"/api/v1/servers/{server_id}/clients")
    assert listing.json()["clients"] == []
    assert any(event.type is EventType.AWG_CLIENT_DELETED for event in fake_broadcaster)


async def test_get_config_returns_decrypted_plaintext(client, server_id, monkeypatch, fake_broadcaster):
    _patch_agent_client(monkeypatch, _FakeAgent())
    create = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "config_text": _VALID_CONFIG},
    )
    cid = create.json()["id"]

    response = await client.get(f"/api/v1/servers/{server_id}/clients/{cid}/config")
    assert response.status_code == 200
    assert response.text == _VALID_CONFIG
    assert response.headers.get("content-disposition") == 'attachment; filename="us-fast.conf"'


async def test_404_when_client_belongs_to_another_server(client, server_id, monkeypatch, fake_broadcaster):
    _patch_agent_client(monkeypatch, _FakeAgent())
    create = await client.post(
        f"/api/v1/servers/{server_id}/clients",
        json={"name": "us-fast", "config_text": _VALID_CONFIG},
    )
    cid = create.json()["id"]

    # Заведём второй сервер и попытаемся обратиться к чужому client_id
    other = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.99", "port": 7743, "name": "other", "token": "tok2"},
    )
    other_id = other.json()["id"]

    response = await client.get(f"/api/v1/servers/{other_id}/clients/{cid}/config")
    assert response.status_code == 404
