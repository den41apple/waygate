from datetime import datetime, timedelta
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.db import get_session
from server.models import MetricsPoint, Server

router = APIRouter(prefix="/servers/{server_id}/metrics", tags=["metrics"])


class MetricsRange(StrEnum):
    HOUR = "1h"  # последний час
    SIX_HOURS = "6h"  # последние 6 часов
    DAY = "24h"  # последние 24 часа


_RANGE_TO_HOURS: dict[MetricsRange, int] = {
    MetricsRange.HOUR: 1,
    MetricsRange.SIX_HOURS: 6,
    MetricsRange.DAY: 24,
}


class MetricsPointResponse(BaseModel):
    timestamp: datetime
    rx_bytes: int
    tx_bytes: int


class MetricsRangeResponse(BaseModel):
    range: MetricsRange
    points: list[MetricsPointResponse]


@router.get("", response_model=MetricsRangeResponse)
async def get_metrics(
    server_id: int,
    range: MetricsRange = Query(default=MetricsRange.HOUR),
    session: AsyncSession = Depends(get_session),
) -> MetricsRangeResponse:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    cutoff = datetime.now() - timedelta(hours=_RANGE_TO_HOURS[range])
    result = await session.execute(
        select(MetricsPoint)
        .where(MetricsPoint.server_id == server_id, MetricsPoint.timestamp >= cutoff)
        .order_by(MetricsPoint.timestamp),
    )
    points = [
        MetricsPointResponse(timestamp=point.timestamp, rx_bytes=point.rx_bytes, tx_bytes=point.tx_bytes)
        for point in result.scalars().all()
    ]
    return MetricsRangeResponse(range=range, points=points)
