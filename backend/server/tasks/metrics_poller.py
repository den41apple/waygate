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


async def _poll_server(*, server: Server, session: AsyncSession) -> None:
    """Тянет /v1/metrics, пишет точку и шлёт SERVER_METRICS.

    Статусом server'а не управляет — это ответственность healthcheck-таски.
    Если агент недоступен, тихо логируем и идём дальше.
    """
    if server.id is None:
        return
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        snapshot = await client.metrics()
    except (AgentUnreachable, AgentClientError) as exc:
        logger.debug("metrics poller: server={} недоступен: {}", server.host, exc)
        return

    rx_total = sum(tunnel.rx_bytes for tunnel in snapshot.tunnels)
    tx_total = sum(tunnel.tx_bytes for tunnel in snapshot.tunnels)
    session.add(
        MetricsPoint(
            server_id=server.id,
            timestamp=snapshot.timestamp,
            rx_bytes=rx_total,
            tx_bytes=tx_total,
        ),
    )
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_METRICS,
            server_id=server.id,
            payload={
                "timestamp": snapshot.timestamp.isoformat(),
                "rx_bytes": rx_total,
                "tx_bytes": tx_total,
            },
            timestamp=datetime.now(tz=UTC),
        ),
    )


async def _retention_cleanup(*, session: AsyncSession) -> None:
    cutoff = datetime.now() - timedelta(days=settings.metrics_retention_days)
    await session.execute(delete(MetricsPoint).where(MetricsPoint.timestamp < cutoff))


async def poll_once(*, session: AsyncSession) -> None:
    """Один проход — собрать метрики со всех серверов, прибраться по retention."""
    result = await session.execute(select(Server))
    servers = list(result.scalars().all())
    for server in servers:
        await _poll_server(server=server, session=session)
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
