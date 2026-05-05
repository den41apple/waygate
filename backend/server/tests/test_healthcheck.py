"""Tests for healthcheck task — reconcile AwgClient.status (BACKLOG #16)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.models import AwgClient, AwgClientStatus, Server, ServerStatus
from server.tasks import healthcheck
from server.ws.events import EventType, WsEvent
from shared.schemas import AgentStatus, AwgContainerInfo, ListAwgClientsResponse
from shared.schemas import AwgClientInfo as AgentAwgClientInfo


class _FakeAgent:
    def __init__(self, *, clients: list[AgentAwgClientInfo]):
        self._clients = clients

    async def status(self):
        return AgentStatus(
            version="0.2.29",
            uptime_seconds=10,
            hostname="test-host",
            awg_containers=[AwgContainerInfo(name="awg-test", interface="awg0")],
            rules_applied=0,
        )

    async def list_clients(self):
        return ListAwgClientsResponse(clients=self._clients)


def _make_agent_info(*, name: str, status: AwgClientStatus) -> AgentAwgClientInfo:
    return AgentAwgClientInfo(
        name=name,
        container_name=f"waygate-amnezia-client-{name}",
        interface_name=f"awg-{name[:11]}",
        status=status,
        peer_endpoint=None,
        peer_pubkey=None,
        interface_address=None,
    )


@pytest.fixture
def fake_broadcaster(monkeypatch):
    events: list[WsEvent] = []

    class _FakeManager:
        async def broadcast(self, *, event):
            events.append(event)

    fake = _FakeManager()
    monkeypatch.setattr(healthcheck, "get_manager", lambda: fake)
    return events


async def _seed_server_and_client(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    db_status: str,
) -> tuple[int, int]:
    async with session_maker() as session:
        server = Server(
            host="10.0.0.1",
            port=7743,
            name="edge",
            token="tok",
            status=ServerStatus.ONLINE.value,
            last_seen_at=datetime.now(tz=UTC),
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        client = AwgClient(
            server_id=server.id,
            name="my-client",
            container_name="waygate-amnezia-client-my-client",
            config_encrypted=b"",
            status=db_status,
        )
        session.add(client)
        await session.commit()
        await session.refresh(client)
        assert server.id is not None
        assert client.id is not None
        return server.id, client.id


async def test_reconcile_updates_db_when_real_status_differs(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """DB говорит running, контейнер в реальности stopped → DB должна стать stopped."""
    _server_id, client_id = await _seed_server_and_client(
        session_maker,
        db_status=AwgClientStatus.RUNNING.value,
    )
    fake = _FakeAgent(
        clients=[_make_agent_info(name="my-client", status=AwgClientStatus.STOPPED)],
    )
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    async with session_maker() as session:
        result = await session.execute(select(AwgClient).where(AwgClient.id == client_id))
        updated = result.scalar_one()
        assert updated.status == AwgClientStatus.STOPPED.value

    types = [event.type for event in fake_broadcaster]
    assert EventType.AWG_CLIENT_STATUS_CHANGED in types
    payload = next(event.payload for event in fake_broadcaster if event.type is EventType.AWG_CLIENT_STATUS_CHANGED)
    assert payload["status"] == AwgClientStatus.STOPPED.value
    assert payload["previous"] == AwgClientStatus.RUNNING.value


async def test_reconcile_marks_missing_container_as_error(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """Контейнер удалён руками с target → агент его не видит → DB должна стать error."""
    _, client_id = await _seed_server_and_client(
        session_maker,
        db_status=AwgClientStatus.RUNNING.value,
    )
    fake = _FakeAgent(clients=[])  # реальный список пуст
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    async with session_maker() as session:
        result = await session.execute(select(AwgClient).where(AwgClient.id == client_id))
        updated = result.scalar_one()
        assert updated.status == "error"
    assert any(event.type is EventType.AWG_CLIENT_STATUS_CHANGED for event in fake_broadcaster)


async def test_reconcile_no_op_when_status_matches(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """Если DB и реальное состояние совпадают — ни WS-event, ни UPDATE."""
    _, _ = await _seed_server_and_client(
        session_maker,
        db_status=AwgClientStatus.RUNNING.value,
    )
    fake = _FakeAgent(
        clients=[_make_agent_info(name="my-client", status=AwgClientStatus.RUNNING)],
    )
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    awg_client_events = [event for event in fake_broadcaster if event.type is EventType.AWG_CLIENT_STATUS_CHANGED]
    assert awg_client_events == []
