import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.config import settings
from server.db import get_session
from server.models import (
    AuditEntry,
    DnsRule,
    MetricsPoint,
    RoutingRule,
    Server,
    ServerStatus,
    TlsConfigRow,
)
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import UpdateRequest, UpdateResponse

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


@router.post("/{server_id}/update", response_model=UpdateResponse)
async def update_server(
    server_id: int,
    request: UpdateServerRequest,
    session: AsyncSession = Depends(get_session),
) -> UpdateResponse:
    """Триггерит self-update агента и (опционально) ждёт reconnect с новой версией."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        response = await client.update(request=UpdateRequest(version=request.version, wheel_url=request.wheel_url))
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("update: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if request.wait_for_reconnect:
        # Ждём пока агент перезапустится и status.version совпадёт с целевой
        deadline_seconds = settings.provision_healthcheck_timeout_seconds
        new_version = await _wait_for_version(
            client=client,
            target_version=request.version,
            timeout_seconds=deadline_seconds,
        )
        if new_version is not None:
            server.version = new_version
            server.status = ServerStatus.ONLINE.value
            await session.commit()
            await get_manager().broadcast(
                event=WsEvent(
                    type=EventType.SERVER_AGENT_UPDATED,
                    server_id=server_id,
                    payload={
                        "previous_version": response.previous_version,
                        "version": new_version,
                    },
                    timestamp=datetime.now(tz=UTC),
                ),
            )
    return response


async def _wait_for_version(*, client: AgentClient, target_version: str, timeout_seconds: int) -> str | None:
    """Polling /v1/status пока version не совпадёт с target. None при таймауте."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            status_response = await client.status()
        except (AgentUnreachable, AgentClientError):
            await asyncio.sleep(2)
            continue
        if status_response.version == target_version:
            return status_response.version
        await asyncio.sleep(2)
    return None


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
