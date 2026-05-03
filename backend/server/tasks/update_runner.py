"""Оркестрация self-update агента: POST /v1/update + ожидание reconnect.

Работает как background-таска, эмитит шаги в `UpdateJob`. UI рендерит лог через
SSE, аналогично онбордингу. Внутренние шаги агента (download, pip install,
restart) не видны напрямую — control-plane эмиттит макроступы того, что он
делает с агентом.
"""

import asyncio
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.config import settings
from server.models import Server, ServerStatus
from server.update_registry import UpdateEventType, UpdateJob
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import UpdateRequest

# Polling-интервал для опроса /v1/status во время ожидания reconnect.
_RECONNECT_POLL_INTERVAL_SECONDS = 2


async def _load_agent_client(
    *,
    server_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> tuple[AgentClient, str, int] | None:
    """Возвращает client + (host, port) для use в emit-сообщениях."""
    async with session_maker() as session:
        server = await session.get(Server, server_id)
        if server is None:
            return None
        return (
            AgentClient(host=server.host, port=server.port, token=server.token),
            server.host,
            server.port,
        )


async def _wait_for_target_version(
    *,
    job: UpdateJob,
    client: AgentClient,
    target_version: str,
) -> str | None:
    """Polling /v1/status пока version не совпадёт с target. Возвращает None при таймауте."""
    timeout_seconds = settings.provision_healthcheck_timeout_seconds
    max_attempts = max(1, int(timeout_seconds // _RECONNECT_POLL_INTERVAL_SECONDS))
    await job.emit(
        type=UpdateEventType.PROGRESS,
        message=f"Жду пока агент перезапустится с {target_version} (≈{timeout_seconds} сек)…",
    )

    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(_RECONNECT_POLL_INTERVAL_SECONDS)
        try:
            status = await client.status()
        except (AgentUnreachable, AgentClientError):
            # Reinstall+restart обычно занимает 5-15 сек, в это окно агент недоступен —
            # это нормально, не emit'им каждое тиканье чтобы не засорять лог.
            if attempt % 5 == 0:
                await job.emit(
                    type=UpdateEventType.PROGRESS,
                    message=f"Агент пока не отвечает (попытка {attempt}/{max_attempts})",
                )
            continue
        if status.version == target_version:
            return status.version
        if attempt % 5 == 0:
            await job.emit(
                type=UpdateEventType.PROGRESS,
                message=(f"Агент отвечает, но версия пока {status.version} (попытка {attempt}/{max_attempts})"),
            )
    return None


async def _persist_version(
    *,
    server_id: int,
    confirmed_version: str,
    previous_version: str,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        server = await session.get(Server, server_id)
        if server is not None:
            server.version = confirmed_version
            server.status = ServerStatus.ONLINE.value
            await session.commit()

    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_AGENT_UPDATED,
            server_id=server_id,
            payload={"previous_version": previous_version, "version": confirmed_version},
            timestamp=datetime.now(tz=UTC),
        ),
    )


async def run_update(
    *,
    job: UpdateJob,
    server_id: int,
    target_version: str,
    wheel_url: str,
    wait_for_reconnect: bool,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Background-таска: запросить агента обновиться, опросить статус, обновить БД."""
    loaded = await _load_agent_client(server_id=server_id, session_maker=session_maker)
    if loaded is None:
        await job.emit(type=UpdateEventType.ERROR, message=f"Server id={server_id} не найден")
        await job.finish()
        return
    client, host, port = loaded

    await job.emit(
        type=UpdateEventType.PROGRESS,
        message=f"Запрашиваю агент {host}:{port} обновиться до версии {target_version}",
    )
    try:
        response = await client.update(
            request=UpdateRequest(version=target_version, wheel_url=wheel_url),
        )
    except AgentUnreachable as exc:
        await job.emit(type=UpdateEventType.ERROR, message=f"Агент недоступен: {exc}")
        await job.finish()
        return
    except AgentClientError as exc:
        logger.error("update: агент вернул ошибку: {}", exc)
        await job.emit(type=UpdateEventType.ERROR, message=str(exc))
        await job.finish()
        return

    previous_version = response.previous_version
    await job.emit(
        type=UpdateEventType.PROGRESS,
        message=f"Агент принял запрос. Прежняя версия: {previous_version}",
    )

    if not wait_for_reconnect:
        await job.emit(
            type=UpdateEventType.DONE,
            message=f"Запрос отправлен (без ожидания). Целевая версия: {target_version}",
        )
        await job.finish()
        return

    confirmed_version = await _wait_for_target_version(
        job=job,
        client=client,
        target_version=target_version,
    )
    if confirmed_version is None:
        await job.emit(
            type=UpdateEventType.ERROR,
            message=(
                f"Таймаут: за {settings.provision_healthcheck_timeout_seconds} сек "
                f"агент не вернулся с версией {target_version}. Возможно, ещё "
                f"перезапускается — проверьте через 1-2 минуты."
            ),
        )
        await job.finish()
        return

    await _persist_version(
        server_id=server_id,
        confirmed_version=confirmed_version,
        previous_version=previous_version,
        session_maker=session_maker,
    )
    await job.emit(
        type=UpdateEventType.DONE,
        message=f"Версия {confirmed_version} подтверждена",
    )
    await job.finish()
