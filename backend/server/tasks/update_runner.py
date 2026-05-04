"""Оркестрация update'а агента — два flow:

1. **SSH-update** (приоритет): server открывает SSH к managed-серверу, скачивает
   wheel, делает atomic-swap venv'а, рестартует unit. Это работает в обход
   `ProtectSystem=strict` агентского юнита, который не даёт self-update'у
   писать в `/opt/`. Требует сохранённых SSH-кредов.

2. **Self-update fallback**: HTTP `POST /v1/update` агенту, агент сам пишет
   swap-script и рестартуется. Используется если SSH-кредов нет (legacy-серверы
   до миграции). На системах с `ProtectSystem=strict /opt` он упадёт.

Оба эмиттят через `UpdateJob`, UI рендерит SSE-лог одинаково.
"""

import asyncio
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.auth.secrets import SecretCipherError, decrypt
from server.config import settings
from server.models import Server, ServerStatus
from server.provisioner.agent_update import update_agent_via_ssh
from server.provisioner.ssh import SshError, ssh_connect
from server.update_registry import UpdateEventType, UpdateJob
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import UpdateRequest

# Polling-интервал для опроса /v1/status во время ожидания reconnect.
_RECONNECT_POLL_INTERVAL_SECONDS = 2


async def _load_server_snapshot(
    *,
    server_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> Server | None:
    """Грузим detached-копию Server-row. Используется и для AgentClient, и для SSH-кредов.

    Возвращаем `Server`-объект «отвязанным» от сессии (expire_on_commit=False
    в session_maker), чтобы безопасно читать поля после `__aexit__`.
    """
    async with session_maker() as session:
        return await session.get(Server, server_id)


def _agent_client_for(*, server: Server) -> AgentClient:
    return AgentClient(host=server.host, port=server.port, token=server.token)


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


async def _run_self_update(
    *,
    job: UpdateJob,
    server: Server,
    target_version: str,
    wheel_url: str,
    wait_for_reconnect: bool,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Старый flow: HTTP POST /v1/update агенту, агент сам делает swap.

    Падает на системах с `ProtectSystem=strict /opt` — agent не может писать
    в свой venv. Оставлено как fallback для legacy-серверов без SSH-кредов.
    """
    client = _agent_client_for(server=server)
    await job.emit(
        type=UpdateEventType.PROGRESS,
        message=f"[self-update] Запрашиваю агент {server.host}:{server.port} обновиться до {target_version}",
    )
    try:
        response = await client.update(
            request=UpdateRequest(version=target_version, wheel_url=wheel_url),
        )
    except AgentUnreachable as exc:
        await job.emit(type=UpdateEventType.ERROR, message=f"Агент недоступен: {exc}")
        return
    except AgentClientError as exc:
        logger.error("update: агент вернул ошибку: {}", exc)
        await job.emit(type=UpdateEventType.ERROR, message=str(exc))
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
                f"перезапускается — проверьте через 1-2 минуты. Если повторяется — "
                f"настройте SSH-креды в EditServerModal для server-side update'а."
            ),
        )
        return

    if server.id is not None:
        await _persist_version(
            server_id=server.id,
            confirmed_version=confirmed_version,
            previous_version=previous_version,
            session_maker=session_maker,
        )
    await job.emit(
        type=UpdateEventType.DONE,
        message=f"Версия {confirmed_version} подтверждена",
    )


async def _run_ssh_update(
    *,
    job: UpdateJob,
    server: Server,
    target_version: str,
    wheel_url: str,
    wait_for_reconnect: bool,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """SSH-flow: server открывает SSH, swap'ает venv пошагово, restart, polling."""
    # Дешифруем сохранённые креды.
    try:
        password = decrypt(token=server.ssh_password_encrypted) if server.ssh_password_encrypted else None
        private_key = decrypt(token=server.ssh_private_key_encrypted) if server.ssh_private_key_encrypted else None
    except SecretCipherError as exc:
        await job.emit(
            type=UpdateEventType.ERROR,
            message=f"Не удалось расшифровать SSH-креды (SECRET_KEY изменился?): {exc}",
        )
        return

    previous_version = server.version

    async def _emit(message: str) -> None:
        await job.emit(type=UpdateEventType.PROGRESS, message=message)

    await _emit(f"[ssh] Подключаюсь к {server.ssh_user}@{server.host}:{server.ssh_port}…")
    try:
        async with ssh_connect(
            host=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=password,
            private_key=private_key,
        ) as ssh:
            await update_agent_via_ssh(
                ssh=ssh,
                wheel_url=wheel_url,
                version=target_version,
                emit=_emit,
            )
    except SshError as exc:
        logger.error("update via ssh: {}", exc)
        await job.emit(type=UpdateEventType.ERROR, message=f"SSH-операция упала: {exc}")
        return

    if not wait_for_reconnect:
        await job.emit(
            type=UpdateEventType.DONE,
            message=f"Готово (без ожидания). Целевая версия: {target_version}",
        )
        return

    # После SSH-restart polling /v1/status — убеждаемся что новая версия запустилась.
    client = _agent_client_for(server=server)
    confirmed_version = await _wait_for_target_version(
        job=job,
        client=client,
        target_version=target_version,
    )
    if confirmed_version is None:
        await job.emit(
            type=UpdateEventType.ERROR,
            message=(
                f"SSH-команды прошли, но агент не отвечает с версией {target_version} "
                f"за {settings.provision_healthcheck_timeout_seconds} сек."
            ),
        )
        return

    if server.id is not None:
        await _persist_version(
            server_id=server.id,
            confirmed_version=confirmed_version,
            previous_version=previous_version,
            session_maker=session_maker,
        )
    await job.emit(
        type=UpdateEventType.DONE,
        message=f"Версия {confirmed_version} подтверждена",
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
    """Background-таска. Авто-выбор flow: SSH если есть креды, иначе self-update."""
    server = await _load_server_snapshot(server_id=server_id, session_maker=session_maker)
    if server is None:
        await job.emit(type=UpdateEventType.ERROR, message=f"Server id={server_id} не найден")
        await job.finish()
        return

    has_ssh_creds = bool(server.ssh_password_encrypted or server.ssh_private_key_encrypted)
    try:
        if has_ssh_creds:
            await _run_ssh_update(
                job=job,
                server=server,
                target_version=target_version,
                wheel_url=wheel_url,
                wait_for_reconnect=wait_for_reconnect,
                session_maker=session_maker,
            )
        else:
            await _run_self_update(
                job=job,
                server=server,
                target_version=target_version,
                wheel_url=wheel_url,
                wait_for_reconnect=wait_for_reconnect,
                session_maker=session_maker,
            )
    finally:
        await job.finish()
