import asyncio
import time
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.api._apply_helper import run_full_apply
from server.config import settings
from server.models import AwgClient, Server, ServerStatus
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import AgentStatus

# Дедуп auto-reapply чтобы не дрожать при flapping ONLINE↔OFFLINE.
# Per-server timestamp последней auto-reapply попытки.
_AUTO_REAPPLY_THROTTLE_SECONDS = 60.0
_last_auto_reapply_at: dict[int, float] = {}


async def _broadcast_status_change(
    *,
    server_id: int,
    new_status: str,
    previous_status: str,
) -> None:
    if previous_status == new_status:
        return
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_STATUS_CHANGED,
            server_id=server_id,
            payload={"status": new_status, "previous": previous_status},
            timestamp=datetime.now(tz=UTC),
        ),
    )


async def _broadcast_agent_updated(
    *,
    server_id: int,
    version: str,
    awg_containers: list[str],
) -> None:
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_AGENT_UPDATED,
            server_id=server_id,
            payload={"version": version, "awg_containers": awg_containers},
            timestamp=datetime.now(tz=UTC),
        ),
    )


async def _auto_reapply_if_needed(
    *,
    server: Server,
    session: AsyncSession,
    client: AgentClient,
    agent_status: AgentStatus,
    previous_status: str,
) -> None:
    """Авто-повтор apply при OFFLINE→ONLINE если у агента есть незакрытые ошибки.

    Закрывает кейс из SESSION_2026_05_06: agent упал на race "Cannot find device
    awg-X" в середине apply, ipset'ы созданы но MASQ не применилось. Любые
    последующие явные apply из UI идемпотентны и не докатывают остаток. Server
    при следующем healthcheck с last_apply_succeeded=False и переходе ONLINE
    — сам триггерит full apply.

    Throttle 60s per-server чтобы не дрожать при flapping. last_apply_errors
    защищён getattr'ом — старые агенты до версии с этими полями отдают default
    `succeeded=True` через `AgentStatus` model_validate.
    """
    if server.id is None:
        return
    succeeded = getattr(agent_status, "last_apply_succeeded", True)
    errors = getattr(agent_status, "last_apply_errors", [])
    needs_reapply = (previous_status == ServerStatus.OFFLINE.value) and (not succeeded or bool(errors))
    if not needs_reapply:
        return

    now = time.monotonic()
    last_at = _last_auto_reapply_at.get(server.id, 0.0)
    if now - last_at < _AUTO_REAPPLY_THROTTLE_SECONDS:
        logger.debug(
            "healthcheck: auto-reapply server={} пропущен (throttle: {:.0f}s назад)",
            server.host,
            now - last_at,
        )
        return
    _last_auto_reapply_at[server.id] = now

    logger.info(
        "healthcheck: auto-reapply server={} (previous={} errors={})",
        server.host,
        previous_status,
        errors,
    )
    try:
        response = await run_full_apply(server=server, session=session, client=client)
    except (AgentUnreachable, AgentClientError) as exc:
        logger.warning("healthcheck: auto-reapply server={} упал: {}", server.host, exc)
        return
    if response.errors:
        logger.warning(
            "healthcheck: auto-reapply server={} вернул {} ошибок: {}",
            server.host,
            len(response.errors),
            response.errors,
        )
    else:
        logger.info(
            "healthcheck: auto-reapply server={} успешен, applied={}",
            server.host,
            response.applied,
        )


async def _reconcile_awg_clients(
    *,
    server: Server,
    session: AsyncSession,
    agent: AgentClient,
) -> None:
    """Синхронизирует `AwgClient.status` в БД с реальным состоянием docker-контейнеров.

    Без этого DB может говорить `running`, в то время как контейнер давно
    `stopped`/`error` (например, оператор сделал `docker rm` руками или клиент
    вылетел на старте). UI показывает stale-«active» → пользователь ожидает что
    apply routing-правил пройдёт, и получает «netdev awg-X не найден» при первом
    же apply. См. BACKLOG #16.
    """
    if server.id is None:
        return
    try:
        listing = await agent.list_clients()
    except (AgentUnreachable, AgentClientError) as exc:
        logger.debug("healthcheck: list_clients server={} не удался: {}", server.host, exc)
        return

    real_status_by_name: dict[str, str] = {info.name: info.status.value for info in listing.clients}

    db_result = await session.execute(select(AwgClient).where(AwgClient.server_id == server.id))
    db_clients = list(db_result.scalars().all())

    for client_record in db_clients:
        previous = client_record.status
        # Если контейнер пропал на target — это явно ERROR-state (не STOPPED:
        # stopped означает «есть, но не запущен», а тут его нет вообще).
        new_status = real_status_by_name.get(client_record.name, "error")
        if new_status == previous:
            continue
        client_record.status = new_status
        await get_manager().broadcast(
            event=WsEvent(
                type=EventType.AWG_CLIENT_STATUS_CHANGED,
                server_id=server.id,
                payload={
                    "id": client_record.id,
                    "name": client_record.name,
                    "status": new_status,
                    "previous": previous,
                },
                timestamp=datetime.now(tz=UTC),
            ),
        )


async def _check_server(*, server: Server, session: AsyncSession) -> None:
    """Лёгкий пинг агента — нужен чтобы быстро ловить online/offline-переходы.

    Не таскает метрики (это работа metrics_poller'а), только status + version + контейнеры.
    """
    if server.id is None:
        return
    previous_status = server.status
    previous_version = server.version
    previous_containers = list(server.awg_containers)

    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        agent_status = await client.status()
    except (AgentUnreachable, AgentClientError) as exc:
        logger.debug("healthcheck: server={} недоступен: {}", server.host, exc)
        server.status = ServerStatus.OFFLINE.value
        await _broadcast_status_change(
            server_id=server.id,
            new_status=ServerStatus.OFFLINE.value,
            previous_status=previous_status,
        )
        return

    server.version = agent_status.version
    server.awg_containers = [container.name for container in agent_status.awg_containers]
    server.status = ServerStatus.ONLINE.value
    server.last_seen_at = datetime.now()

    await _broadcast_status_change(
        server_id=server.id,
        new_status=ServerStatus.ONLINE.value,
        previous_status=previous_status,
    )
    if previous_version != server.version or previous_containers != server.awg_containers:
        await _broadcast_agent_updated(
            server_id=server.id,
            version=server.version,
            awg_containers=list(server.awg_containers),
        )
    # OFFLINE→ONLINE с незакрытыми ошибками apply на агенте → авто-повтор.
    # Делаем ДО reconcile_awg_clients: успешный apply сам перезапустит контейнеры
    # в нужный network_mode и перезапись AwgClient.status пройдёт чище.
    await _auto_reapply_if_needed(
        server=server,
        session=session,
        client=client,
        agent_status=agent_status,
        previous_status=previous_status,
    )
    # Сервер онлайн → reconcile AwgClient.status с реальным состоянием docker'а.
    # Делаем после status-broadcast'а: даже если list_clients упадёт, online уже
    # отправлено наверх.
    await _reconcile_awg_clients(server=server, session=session, agent=client)


async def healthcheck_once(*, session: AsyncSession) -> None:
    result = await session.execute(select(Server))
    servers = list(result.scalars().all())
    for server in servers:
        await _check_server(server=server, session=session)
    await session.commit()


async def healthcheck_loop(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    interval_seconds: int | None = None,
) -> None:
    """Фоновый цикл healthcheck. Существует отдельно от metrics-poller'а — лёгкий пинг."""
    interval = interval_seconds or settings.healthcheck_seconds
    logger.info("healthcheck: старт, интервал {} сек", interval)
    while True:
        try:
            async with session_maker() as session:
                await healthcheck_once(session=session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("healthcheck: цикл прерван исключением: {}", exc)
        await asyncio.sleep(interval)
