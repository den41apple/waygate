"""Tests for healthcheck task — reconcile AwgClient.status (BACKLOG #16)
+ auto-reapply при OFFLINE→ONCE (SESSION_2026_05_06)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.models import AwgClient, AwgClientStatus, RoutingRule, Server, ServerStatus
from server.tasks import healthcheck
from server.ws.events import EventType, WsEvent
from shared.schemas import (
    AgentStatus,
    ApplyRulesRequest,
    ApplyRulesResponse,
    AwgContainerInfo,
    ListAwgClientsResponse,
)
from shared.schemas import AwgClientInfo as AgentAwgClientInfo


class _FakeAgent:
    def __init__(
        self,
        *,
        clients: list[AgentAwgClientInfo],
        last_apply_errors: list[str] | None = None,
        last_apply_succeeded: bool = True,
    ):
        self._clients = clients
        self._last_apply_errors = last_apply_errors or []
        self._last_apply_succeeded = last_apply_succeeded
        self.dns_applies = 0
        self.ipset_applies = 0
        self.rule_applies: list[ApplyRulesRequest] = []

    async def status(self):
        return AgentStatus(
            version="0.2.29",
            uptime_seconds=10,
            hostname="test-host",
            awg_containers=[AwgContainerInfo(name="awg-test", interface="awg0")],
            rules_applied=0,
            last_apply_errors=list(self._last_apply_errors),
            last_apply_succeeded=self._last_apply_succeeded,
        )

    async def list_clients(self):
        return ListAwgClientsResponse(clients=self._clients)

    async def apply_dns(self, *, request):
        self.dns_applies += 1
        return ApplyRulesResponse(applied=len(request.rules), skipped=0, errors=[])

    async def apply_custom_ipset(self, *, request):
        self.ipset_applies += 1
        return ApplyRulesResponse(applied=1, skipped=0, errors=[])

    async def apply_rules(self, *, request):
        self.rule_applies.append(request)
        return ApplyRulesResponse(applied=len(request.rules), skipped=0, errors=[])


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
    # Auto-reapply вызывает run_full_apply из _apply_helper, который имеет свой
    # импорт get_manager — патчим обе точки чтобы видеть RULE_APPLIED событие.
    from server.api import _apply_helper as apply_helper

    monkeypatch.setattr(apply_helper, "get_manager", lambda: fake)
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


@pytest.fixture(autouse=True)
def _reset_auto_reapply_throttle():
    """Сбрасывать дедуп между тестами — иначе порядок запуска влияет на результат."""
    healthcheck._last_auto_reapply_at.clear()
    yield
    healthcheck._last_auto_reapply_at.clear()


async def test_auto_reapply_on_offline_to_online_with_errors(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """Закрывает SESSION_2026_05_06: agent после рестарта/восстановления имеет
    last_apply_errors!=[], server при следующем healthcheck автоматически
    повторяет full apply без явного нажатия пользователем."""
    # Сервер в OFFLINE state'е + один enabled RoutingRule в БД.
    async with session_maker() as session:
        server = Server(
            host="10.0.0.1",
            port=7743,
            name="edge",
            token="tok",
            status=ServerStatus.OFFLINE.value,
            last_seen_at=datetime.now(tz=UTC),
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        rule = RoutingRule(
            server_id=server.id,
            country="--",
            ipset_name="all-internet",
            fwmark=42,
            table_id=142,
            via_interface="awg-eurohoster",
            via_gateway="10.8.1.0",
            enabled=True,
        )
        session.add(rule)
        await session.commit()

    fake = _FakeAgent(
        clients=[],
        last_apply_errors=["[host/v4] apply all-internet-v4: Cannot find device"],
        last_apply_succeeded=False,
    )
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    # Auto-reapply должен был дёрнуть apply_rules с нашим routing-правилом.
    assert len(fake.rule_applies) == 1, f"ожидался 1 auto-reapply, было {len(fake.rule_applies)}"
    assert fake.rule_applies[0].rules[0].via_interface == "awg-eurohoster"
    # И broadcast RULE_APPLIED событие отправилось.
    rule_applied = [event for event in fake_broadcaster if event.type is EventType.RULE_APPLIED]
    assert len(rule_applied) == 1


async def test_auto_reapply_skipped_when_succeeded(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """Если у агента нет ошибок (succeeded=True) — auto-reapply не запускается,
    даже при OFFLINE→ONLINE. Чтобы не дёргать apply при штатных рестартах."""
    async with session_maker() as session:
        server = Server(
            host="10.0.0.1",
            port=7743,
            name="edge",
            token="tok",
            status=ServerStatus.OFFLINE.value,
            last_seen_at=datetime.now(tz=UTC),
        )
        session.add(server)
        await session.commit()

    fake = _FakeAgent(clients=[], last_apply_errors=[], last_apply_succeeded=True)
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    assert fake.rule_applies == []


async def test_auto_reapply_throttle_dedupes(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
):
    """Если за <60s OFFLINE-ONLINE flap случился дважды — auto-reapply
    дёрнется только один раз. Защита от дрожания."""
    async with session_maker() as session:
        server = Server(
            host="10.0.0.1",
            port=7743,
            name="edge",
            token="tok",
            status=ServerStatus.OFFLINE.value,
            last_seen_at=datetime.now(tz=UTC),
        )
        session.add(server)
        await session.commit()

    fake = _FakeAgent(
        clients=[],
        last_apply_errors=["something broken"],
        last_apply_succeeded=False,
    )
    monkeypatch.setattr(healthcheck, "AgentClient", lambda **_kw: fake)

    # Первый pass: ONLINE-переход → auto-reapply.
    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)
    # Второй pass сразу после: status уже ONLINE → не auto-reapply (нет previous=OFFLINE).
    # Принудительно сбрасываем status обратно в OFFLINE и попробуем снова: throttle спасёт.
    async with session_maker() as session:
        result = await session.execute(select(Server))
        server_record = result.scalar_one()
        server_record.status = ServerStatus.OFFLINE.value
        await session.commit()
    async with session_maker() as session:
        await healthcheck.healthcheck_once(session=session)

    # Только один реальный auto-reapply за <60s.
    assert len(fake.rule_applies) == 1
