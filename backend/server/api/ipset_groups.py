"""CRUD для пользовательских ipset-групп (custom CIDR-наборов).

Третья сущность для маршрутизации (наряду с GeoIP-зонами и DNS-правилами):
пользователь явно перечисляет CIDR'ы, агент собирает их в ipset того же имени.

POST/PATCH опционально применяет на агенте через `/v1/ipset/apply` —
параметром `?apply=true`. Иначе изменения остаются только в БД до явного
вызова apply (следующий шаг — кнопка «Применить» в UI).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import IpsetGroup, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import IpsetApplyRequest, IpsetName

router = APIRouter(prefix="/servers/{server_id}/ipset-groups", tags=["ipset-groups"])


class IpsetGroupCreate(BaseModel):
    name: IpsetName = Field(description="Имя ipset на агенте")
    cidrs: list[str] = Field(default_factory=list, description="Список CIDR/IP")


class IpsetGroupUpdate(BaseModel):
    name: IpsetName | None = None
    cidrs: list[str] | None = None


class IpsetGroupResponse(BaseModel):
    id: int
    server_id: int
    name: str
    cidrs: list[str]
    created_at: datetime
    updated_at: datetime


class IpsetGroupListResponse(BaseModel):
    groups: list[IpsetGroupResponse]


def _to_response(*, group: IpsetGroup) -> IpsetGroupResponse:
    if group.id is None:
        raise RuntimeError("IpsetGroup.id None после persist")
    return IpsetGroupResponse(
        id=group.id,
        server_id=group.server_id,
        name=group.name,
        cidrs=list(group.cidrs),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _load_server(*, server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return server


async def _broadcast(*, type: EventType, server_id: int, payload: dict[str, object]) -> None:
    """Шлёт WS-event подписчикам — фронт инвалидирует список без F5."""
    await get_manager().broadcast(
        event=WsEvent(
            type=type,
            server_id=server_id,
            payload=payload,
            timestamp=datetime.now(tz=UTC),
        ),
    )


async def _maybe_apply(*, server: Server, group: IpsetGroup, apply: bool) -> None:
    """Если `?apply=true` — отправляем CIDR'ы на агента в `/v1/ipset/apply`.

    Best-effort: если агент недоступен — изменения в БД остаются, можно
    позже вручную применить.
    """
    if not apply:
        return
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        await client.apply_custom_ipset(
            request=IpsetApplyRequest(name=group.name, cidrs=list(group.cidrs)),
        )
    except (AgentUnreachable, AgentClientError) as exc:
        logger.warning("ipset_groups.apply: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("", response_model=IpsetGroupListResponse)
async def list_groups(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> IpsetGroupListResponse:
    await _load_server(server_id=server_id, session=session)
    result = await session.execute(
        select(IpsetGroup).where(IpsetGroup.server_id == server_id).order_by(IpsetGroup.id),
    )
    groups = result.scalars().all()
    return IpsetGroupListResponse(groups=[_to_response(group=group) for group in groups])


@router.post("", response_model=IpsetGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    server_id: int,
    request: IpsetGroupCreate,
    apply: bool = Query(default=True, description="Сразу применить на агенте"),
    session: AsyncSession = Depends(get_session),
) -> IpsetGroupResponse:
    server = await _load_server(server_id=server_id, session=session)
    group = IpsetGroup(server_id=server_id, name=request.name, cidrs=list(request.cidrs))
    session.add(group)
    await session.commit()
    await session.refresh(group)
    await _maybe_apply(server=server, group=group, apply=apply)
    await _broadcast(
        type=EventType.IPSET_GROUP_CREATED,
        server_id=server_id,
        payload={"id": group.id, "name": group.name, "cidrs_count": len(group.cidrs)},
    )
    return _to_response(group=group)


async def _get_group(*, server_id: int, group_id: int, session: AsyncSession) -> IpsetGroup:
    group = await session.get(IpsetGroup, group_id)
    if group is None or group.server_id != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ipset-group id={group_id} не найден на server_id={server_id}",
        )
    return group


@router.patch("/{group_id}", response_model=IpsetGroupResponse)
async def update_group(
    server_id: int,
    group_id: int,
    request: IpsetGroupUpdate,
    apply: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> IpsetGroupResponse:
    server = await _load_server(server_id=server_id, session=session)
    group = await _get_group(server_id=server_id, group_id=group_id, session=session)
    changed = False
    if request.name is not None and request.name != group.name:
        group.name = request.name
        changed = True
    if request.cidrs is not None:
        group.cidrs = list(request.cidrs)
        changed = True
    if changed:
        group.updated_at = datetime.now()
    try:
        await session.commit()
    except IntegrityError as exc:
        # UNIQUE(server_id, name) — пользователь хочет переименовать в имя,
        # уже занятое другой группой на этом сервере.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ipset-group с именем '{request.name}' уже существует на server_id={server_id}",
        ) from exc
    await session.refresh(group)
    await _maybe_apply(server=server, group=group, apply=apply)
    await _broadcast(
        type=EventType.IPSET_GROUP_UPDATED,
        server_id=server_id,
        payload={"id": group.id, "name": group.name, "cidrs_count": len(group.cidrs)},
    )
    return _to_response(group=group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    server_id: int,
    group_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    group = await _get_group(server_id=server_id, group_id=group_id, session=session)
    deleted_id = group.id
    deleted_name = group.name
    await session.delete(group)
    await session.commit()
    await _broadcast(
        type=EventType.IPSET_GROUP_DELETED,
        server_id=server_id,
        payload={"id": deleted_id, "name": deleted_name},
    )
