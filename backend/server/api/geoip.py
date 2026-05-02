from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import GeoList, Server
from server.models.geo_list import GeoListStatus
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import GeoIpSyncRequest, GeoIpSyncResponse

router = APIRouter(tags=["geoip"])


class GeoListCreate(BaseModel):
    country: str = Field(description="Код страны ISO 3166-1 alpha-2")
    name: str = Field(description="Человекочитаемое имя списка")
    source_url: str = Field(description="URL zone-файла")


class GeoListResponse(BaseModel):
    id: int
    country: str
    name: str
    source_url: str
    ipv4_count: int
    ipv6_count: int
    custom_count: int
    last_synced_at: datetime | None
    status: str


class GeoListListResponse(BaseModel):
    lists: list[GeoListResponse]


class GeoIpSyncTrigger(BaseModel):
    geo_list_id: int = Field(description="id GeoList, который синхронизируем")
    ipset_name: str = Field(description="Куда заливать на агенте")
    custom_cidrs: list[str] = Field(default_factory=list)


def _to_response(*, geo_list: GeoList) -> GeoListResponse:
    if geo_list.id is None:
        raise RuntimeError("GeoList.id None после persist")
    return GeoListResponse(
        id=geo_list.id,
        country=geo_list.country,
        name=geo_list.name,
        source_url=geo_list.source_url,
        ipv4_count=geo_list.ipv4_count,
        ipv6_count=geo_list.ipv6_count,
        custom_count=geo_list.custom_count,
        last_synced_at=geo_list.last_synced_at,
        status=geo_list.status,
    )


@router.get("/geoip/lists", response_model=GeoListListResponse)
async def list_geoip(session: AsyncSession = Depends(get_session)) -> GeoListListResponse:
    result = await session.execute(select(GeoList).order_by(GeoList.country))
    return GeoListListResponse(lists=[_to_response(geo_list=item) for item in result.scalars().all()])


@router.post("/geoip/lists", response_model=GeoListResponse, status_code=status.HTTP_201_CREATED)
async def create_geoip_list(
    request: GeoListCreate,
    session: AsyncSession = Depends(get_session),
) -> GeoListResponse:
    geo_list = GeoList(
        country=request.country,
        name=request.name,
        source_url=request.source_url,
        status=GeoListStatus.STALE.value,
    )
    session.add(geo_list)
    await session.commit()
    await session.refresh(geo_list)
    return _to_response(geo_list=geo_list)


@router.delete("/geoip/lists/{geo_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_geoip_list(
    geo_list_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    geo_list = await session.get(GeoList, geo_list_id)
    if geo_list is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"geo_list id={geo_list_id} не найден")
    await session.delete(geo_list)
    await session.commit()


@router.post("/servers/{server_id}/geoip/sync", response_model=GeoIpSyncResponse)
async def trigger_geoip_sync(
    server_id: int,
    request: GeoIpSyncTrigger,
    session: AsyncSession = Depends(get_session),
) -> GeoIpSyncResponse:
    """Триггер синхронизации списка на агенте: GeoList + ipset_name → POST /v1/geoip/sync."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    geo_list = await session.get(GeoList, request.geo_list_id)
    if geo_list is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"geo_list id={request.geo_list_id} не найден",
        )
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    sync_request = GeoIpSyncRequest(
        country=geo_list.country,
        ipset_name=request.ipset_name,
        source_url=geo_list.source_url,
        custom_cidrs=request.custom_cidrs,
    )
    try:
        response = await client.sync_geoip(request=sync_request)
    except AgentUnreachable as exc:
        geo_list.status = GeoListStatus.ERROR.value
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("geoip sync: агент вернул ошибку: {}", exc)
        geo_list.status = GeoListStatus.ERROR.value
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    geo_list.ipv4_count = response.cidrs_loaded
    geo_list.custom_count = len(request.custom_cidrs)
    geo_list.last_synced_at = datetime.now()
    geo_list.status = GeoListStatus.SYNCED.value
    await session.commit()
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.GEOIP_SYNCED,
            server_id=server_id,
            payload={
                "geo_list_id": geo_list.id,
                "country": geo_list.country,
                "ipset_name": request.ipset_name,
                "cidrs_loaded": response.cidrs_loaded,
                "duration_ms": response.duration_ms,
            },
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return response
