import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.auth.secrets import SecretCipherError, decrypt, encrypt
from server.db import get_session, get_session_maker
from server.models import (
    AuditEntry,
    DnsRule,
    MetricsPoint,
    RoutingRule,
    Server,
    ServerStatus,
    TlsConfigRow,
)
from server.provisioner.ssh import SshError, ssh_connect
from server.provisioner.uninstall import uninstall_agent
from server.tasks.update_runner import run_update
from server.update_registry import get_update_registry
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager

router = APIRouter(prefix="/servers", tags=["servers"])


class ServerCreate(BaseModel):
    host: str = Field(description="DNS-имя или IP агента")
    port: int = Field(default=7743, description="HTTPS-порт агента")
    name: str = Field(description="Человекочитаемое имя сервера")
    token: str = Field(description="Bearer-токен агента")
    region: str | None = Field(default=None, description="Регион/группа для sidebar")


class ServerResponse(BaseModel):
    id: int
    host: str
    port: int
    name: str
    version: str
    status: str
    region: str | None
    awg_containers: list[str]
    added_at: datetime
    last_seen_at: datetime | None
    # SSH-доступ для server-side update'а. Plaintext креды никогда не отдаём;
    # фронт получает только booleans чтобы знать активен ли SSH-flow и какую
    # форму показать в EditServerModal («сменить пароль» vs «удалить ключ»).
    ssh_user: str
    ssh_port: int
    has_ssh_password: bool
    has_ssh_private_key: bool


class ServerListResponse(BaseModel):
    servers: list[ServerResponse]


def _to_response(*, server: Server) -> ServerResponse:
    if server.id is None:
        raise RuntimeError("Server.id None после persist — не должно случаться")
    return ServerResponse(
        id=server.id,
        host=server.host,
        port=server.port,
        name=server.name,
        version=server.version,
        status=server.status,
        region=server.region,
        awg_containers=list(server.awg_containers),
        added_at=server.added_at,
        last_seen_at=server.last_seen_at,
        ssh_user=server.ssh_user,
        ssh_port=server.ssh_port,
        has_ssh_password=server.ssh_password_encrypted is not None,
        has_ssh_private_key=server.ssh_private_key_encrypted is not None,
    )


@router.get("", response_model=ServerListResponse)
async def list_servers(session: AsyncSession = Depends(get_session)) -> ServerListResponse:
    result = await session.execute(select(Server).order_by(Server.id))
    servers = result.scalars().all()
    return ServerListResponse(servers=[_to_response(server=server) for server in servers])


@router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    request: ServerCreate,
    session: AsyncSession = Depends(get_session),
) -> ServerResponse:
    server = Server(
        host=request.host,
        port=request.port,
        name=request.name,
        token=request.token,
        region=request.region,
        status=ServerStatus.OFFLINE.value,
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)
    logger.info("server создан: id={} host={}", server.id, server.host)
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_CREATED,
            server_id=server.id,
            payload={"host": server.host, "name": server.name},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return _to_response(server=server)


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServerResponse:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return _to_response(server=server)


class ServerUpdate(BaseModel):
    """PATCH-обновление настроек Server.

    Что нельзя менять через PATCH: `host`, `port`, `token` — для них либо
    переонбординг (host/port — техническая смена endpoint'а), либо
    `POST /token/rotate` (token).

    SSH-fields (`ssh_password`, `ssh_private_key`) принимаются plaintext и
    шифруются перед сохранением. Пустая строка `""` → `null` в БД (=удалить
    cred). `None` (отсутствие поля в payload) → не трогать.
    """

    name: str | None = None
    region: str | None = None
    ssh_user: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None


@router.patch("/{server_id}", response_model=ServerResponse)
async def patch_server_settings(
    server_id: int,
    request: ServerUpdate,
    session: AsyncSession = Depends(get_session),
) -> ServerResponse:
    """Не путать с `update_server` ниже (self-update агента, POST /{id}/update)."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    payload = request.model_dump(exclude_unset=True)
    # SSH-creds — особый case: plaintext входит, encrypted-token летит в БД.
    # Пустая строка означает «удалить cred» → null в БД.
    if "ssh_password" in payload:
        plain = payload.pop("ssh_password")
        server.ssh_password_encrypted = encrypt(plaintext=plain) if plain else None
    if "ssh_private_key" in payload:
        plain = payload.pop("ssh_private_key")
        server.ssh_private_key_encrypted = encrypt(plaintext=plain) if plain else None
    for field, value in payload.items():
        setattr(server, field, value)
    await session.commit()
    await session.refresh(server)
    logger.info("server обновлён: id={} fields={}", server_id, list(payload.keys()))
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_UPDATED,
            server_id=server_id,
            payload={"name": server.name, "region": server.region},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return _to_response(server=server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    await session.execute(delete(MetricsPoint).where(MetricsPoint.server_id == server_id))
    await session.execute(delete(RoutingRule).where(RoutingRule.server_id == server_id))
    await session.execute(delete(DnsRule).where(DnsRule.server_id == server_id))
    await session.execute(delete(TlsConfigRow).where(TlsConfigRow.server_id == server_id))
    # AuditEntry — историческая запись, удалять её жалко. server_id у неё nullable,
    # обнуляем — FK довольна, аудит сохраняется.
    await session.execute(
        update(AuditEntry).where(AuditEntry.server_id == server_id).values(server_id=None),
    )
    await session.delete(server)
    await session.commit()
    logger.info("server удалён: id={}", server_id)
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_DELETED,
            server_id=server_id,
            payload={},
            timestamp=datetime.now(tz=UTC),
        ),
    )


class UpdateServerRequest(BaseModel):
    version: str = Field(description="Целевая версия агента (например 0.2.0)")
    wheel_url: str = Field(description="URL wheel-файла на GitHub Releases")
    wait_for_reconnect: bool = Field(default=True, description="Дождаться появления новой версии")


class UpdateStartResponse(BaseModel):
    server_id: int
    target_version: str
    stream_url: str = Field(
        description="Относительный URL для подписки на лог апдейта через EventSource",
    )


@router.post(
    "/{server_id}/update",
    response_model=UpdateStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_server(
    server_id: int,
    request: UpdateServerRequest,
    session: AsyncSession = Depends(get_session),
) -> UpdateStartResponse:
    """Запускает self-update агента в background-таске. Прогресс отдаётся через
    `GET /servers/{id}/update/stream` (SSE), чтобы UI мог рендерить лог как при
    онбординге, а HTTP-запрос не висел десятки секунд в ожидании reconnect."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")

    registry = get_update_registry()
    try:
        job = await registry.create(server_id=server_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    job.task = asyncio.create_task(
        run_update(
            job=job,
            server_id=server_id,
            target_version=request.version,
            wheel_url=request.wheel_url,
            wait_for_reconnect=request.wait_for_reconnect,
            session_maker=get_session_maker(),
        ),
    )
    logger.info("update: запущен server_id={} target={}", server_id, request.version)

    return UpdateStartResponse(
        server_id=server_id,
        target_version=request.version,
        stream_url=f"/api/v1/servers/{server_id}/update/stream",
    )


def _format_sse(*, payload: dict[str, object]) -> str:
    # ensure_ascii=False — чтобы русский лог не приходил escaped как Аг...
    return f"data: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"


@router.get("/{server_id}/update/stream")
async def update_stream(server_id: int) -> StreamingResponse:
    """SSE-стрим лога self-update'а. Реплеит историю + стримит до finish."""
    job = get_update_registry().get(server_id=server_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"для server_id={server_id} нет активного update'а",
        )

    async def event_generator() -> AsyncIterator[str]:
        async for event in job.subscribe():
            yield _format_sse(
                payload={
                    "type": event.type.value,
                    "message": event.message,
                    "timestamp": event.timestamp.isoformat(),
                },
            )
        yield _format_sse(payload={"type": "end"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class RotateTokenResponse(BaseModel):
    rotated: bool = Field(description="Удалось ли перезаписать токен")


@router.post("/{server_id}/token/rotate", response_model=RotateTokenResponse)
async def rotate_server_token(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> RotateTokenResponse:
    """Запрашивает у агента новый Bearer-токен и сохраняет его в БД.

    Старый токен моментально становится невалидным — агент перезаписывает env-файл
    и обновляет settings.token в памяти ДО ответа.
    """
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        response = await client.rotate_token()
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("rotate_token: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    server.token = response.token
    await session.commit()
    logger.info("rotate_token: новый токен записан для server_id={}", server_id)
    return RotateTokenResponse(rotated=True)


@router.post("/{server_id}/refresh", response_model=ServerResponse)
async def refresh_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServerResponse:
    """Опрашивает агент и обновляет version/awg_containers/status/last_seen_at в БД."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    previous_status = server.status
    previous_version = server.version
    previous_containers = list(server.awg_containers)
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        agent_status = await client.status()
    except AgentUnreachable as exc:
        server.status = ServerStatus.OFFLINE.value
        await session.commit()
        await session.refresh(server)
        if previous_status != ServerStatus.OFFLINE.value:
            await get_manager().broadcast(
                event=WsEvent(
                    type=EventType.SERVER_STATUS_CHANGED,
                    server_id=server_id,
                    payload={"status": ServerStatus.OFFLINE.value, "previous": previous_status},
                    timestamp=datetime.now(tz=UTC),
                ),
            )
        logger.warning("refresh: агент {} недоступен: {}", server.host, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"агент недоступен: {exc}",
        ) from exc
    server.version = agent_status.version
    server.awg_containers = [container.name for container in agent_status.awg_containers]
    server.status = ServerStatus.ONLINE.value
    server.last_seen_at = datetime.now()
    await session.commit()
    await session.refresh(server)
    if previous_status != ServerStatus.ONLINE.value:
        await get_manager().broadcast(
            event=WsEvent(
                type=EventType.SERVER_STATUS_CHANGED,
                server_id=server_id,
                payload={"status": ServerStatus.ONLINE.value, "previous": previous_status},
                timestamp=datetime.now(tz=UTC),
            ),
        )
    if previous_version != server.version or previous_containers != server.awg_containers:
        await get_manager().broadcast(
            event=WsEvent(
                type=EventType.SERVER_AGENT_UPDATED,
                server_id=server_id,
                payload={"version": server.version, "awg_containers": list(server.awg_containers)},
                timestamp=datetime.now(tz=UTC),
            ),
        )
    return _to_response(server=server)


class UninstallResponse(BaseModel):
    """Результат полного удаления waygate-agent с target-сервера."""

    server_id: int
    log: list[str] = Field(description="Прогресс-лог cleanup'а на target-VM")


@router.post("/{server_id}/uninstall", response_model=UninstallResponse)
async def uninstall_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> UninstallResponse:
    """Полностью удаляет waygate-agent с target-сервера + удаляет server из БД.

    Требует сохранённых SSH-credentials (через `PATCH /servers/{id}` с
    ssh_password или ssh_private_key). Если кредов нет — 400.

    Что делает на target:
    - systemctl stop/disable waygate-agent
    - docker rm -f всех waygate-amnezia-client-*
    - flush iptables mangle/nat (только waygate-related)
    - flush ip rule fwmark + custom routing tables
    - destroy waygate-managed ipset'ы (geoip-*, dns-*, *-v4/v6)
    - rm -rf /opt/waygate-agent /etc/waygate /var/lib/waygate-agent /etc/amnezia
    - rm /etc/dnsmasq.d/waygate.conf + restart dnsmasq
    - rm /etc/systemd/system/waygate-agent.service + daemon-reload

    После успешного uninstall'а server-запись удаляется из БД (как `DELETE /{id}`).
    """
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    if not server.ssh_password_encrypted and not server.ssh_private_key_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Для uninstall'а нужны сохранённые SSH-credentials. "
                "Установи пароль/ключ через PATCH /servers/{id} и попробуй снова."
            ),
        )

    try:
        password = decrypt(token=server.ssh_password_encrypted) if server.ssh_password_encrypted else None
        private_key = decrypt(token=server.ssh_private_key_encrypted) if server.ssh_private_key_encrypted else None
    except SecretCipherError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось расшифровать SSH-креды (SECRET_KEY изменился?): {exc}",
        ) from exc

    log: list[str] = []

    async def _emit(message: str) -> None:
        log.append(message)

    try:
        async with ssh_connect(
            host=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=password,
            private_key=private_key,
        ) as ssh:
            await uninstall_agent(ssh=ssh, emit=_emit)
    except SshError as exc:
        logger.error("uninstall via ssh: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH-операция упала: {exc}",
        ) from exc

    # После успешного cleanup'а на target — удаляем server из БД (та же логика что delete_server).
    await session.execute(delete(MetricsPoint).where(MetricsPoint.server_id == server_id))
    await session.execute(delete(RoutingRule).where(RoutingRule.server_id == server_id))
    await session.execute(delete(DnsRule).where(DnsRule.server_id == server_id))
    await session.execute(delete(TlsConfigRow).where(TlsConfigRow.server_id == server_id))
    await session.execute(
        update(AuditEntry).where(AuditEntry.server_id == server_id).values(server_id=None),
    )
    await session.delete(server)
    await session.commit()
    logger.info("server uninstalled: id={}, log lines={}", server_id, len(log))
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_DELETED,
            server_id=server_id,
            payload={"reason": "uninstalled"},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return UninstallResponse(server_id=server_id, log=log)
