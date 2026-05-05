import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import delete, select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.config import settings
from server.models import MetricsPoint, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import MetricsSnapshot


async def _fetch_metrics(*, server: Server) -> tuple[int, MetricsSnapshot] | None:
    """HTTP-only fetch /v1/metrics. Возвращает (server_id, snapshot) или None.

    Намеренно не трогает БД — это позволяет вызывать N штук параллельно через
    `asyncio.gather` без conflict'а на shared `AsyncSession` (sqlalchemy сессия
    не concurrent-safe). Запись и broadcast делает poll_once sequentially.
    """
    if server.id is None:
        return None
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        snapshot = await client.metrics()
    except (AgentUnreachable, AgentClientError) as exc:
        logger.debug("metrics poller: server={} недоступен: {}", server.host, exc)
        return None
    return server.id, snapshot


def _persist_snapshot(
    *,
    server_id: int,
    snapshot: MetricsSnapshot,
    session: AsyncSession,
) -> dict[str, object]:
    """Записывает MetricsPoint в session и возвращает payload для WS-broadcast."""
    rx_total = sum(tunnel.rx_bytes for tunnel in snapshot.tunnels)
    tx_total = sum(tunnel.tx_bytes for tunnel in snapshot.tunnels)
    # `snapshot.timestamp` приходит aware (Pydantic ISO-8601 → tz-aware), а
    # колонка БД — TIMESTAMP WITHOUT TIME ZONE. Стрипаем TZ к naive UTC, иначе
    # asyncpg на INSERT делает arithmetic aware−naive и падает с TypeError.
    timestamp_naive_utc = (
        snapshot.timestamp.astimezone(UTC).replace(tzinfo=None)
        if snapshot.timestamp.tzinfo is not None
        else snapshot.timestamp
    )
    session.add(
        MetricsPoint(
            server_id=server_id,
            timestamp=timestamp_naive_utc,
            rx_bytes=rx_total,
            tx_bytes=tx_total,
        ),
    )
    return {
        "timestamp": snapshot.timestamp.isoformat(),
        "rx_bytes": rx_total,
        "tx_bytes": tx_total,
    }


async def _retention_cleanup(*, session: AsyncSession) -> None:
    # Колонка `metrics_points.timestamp` — TIMESTAMP WITHOUT TIME ZONE, так что
    # cutoff тоже naive. Сравнение в SQL — между двумя naive UTC datetime'ами.
    cutoff = datetime.now() - timedelta(days=settings.metrics_retention_days)
    await session.execute(delete(MetricsPoint).where(MetricsPoint.timestamp < cutoff))


async def poll_once(*, session: AsyncSession) -> None:
    """Один проход — собрать метрики со всех серверов параллельно, прибраться по retention.

    Раньше polling был последовательный (`for server in servers: await _poll_server`),
    и медленный/недоступный агент задерживал весь цикл. Теперь HTTP-запросы
    идут через `asyncio.gather`, а запись в session и WS-broadcast — sequentially
    (sqlalchemy AsyncSession не concurrent-safe).
    """
    result = await session.execute(select(Server))
    servers = list(result.scalars().all())

    # Fetch'и параллельно. `return_exceptions=True` чтобы один упавший fetch не
    # ронял остальные — `_fetch_metrics` уже сам ловит AgentClientError'ы и
    # возвращает None, но на всякий case (например asyncio.TimeoutError) ловим.
    fetched = await asyncio.gather(
        *(_fetch_metrics(server=server) for server in servers),
        return_exceptions=True,
    )

    manager = get_manager()
    for outcome in fetched:
        if isinstance(outcome, BaseException):
            logger.warning("metrics poller: fetch упал: {}", outcome)
            continue
        if outcome is None:
            continue
        server_id, snapshot = outcome
        payload = _persist_snapshot(server_id=server_id, snapshot=snapshot, session=session)
        await manager.broadcast(
            event=WsEvent(
                type=EventType.SERVER_METRICS,
                server_id=server_id,
                payload=payload,
                timestamp=datetime.now(tz=UTC),
            ),
        )

    await _retention_cleanup(session=session)
    await session.commit()


async def metrics_poller_loop(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    interval_seconds: int | None = None,
) -> None:
    """Фоновый цикл сбора метрик. Запускается из lifespan и работает до отмены."""
    interval = interval_seconds or settings.metrics_poll_seconds
    logger.info("metrics poller: старт, интервал {} сек", interval)
    while True:
        try:
            async with session_maker() as session:
                await poll_once(session=session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("metrics poller: цикл прерван исключением: {}", exc)
        await asyncio.sleep(interval)
