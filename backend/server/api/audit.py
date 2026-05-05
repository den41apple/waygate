from datetime import datetime, timedelta
from enum import StrEnum

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.db import get_session
from server.models import AuditEntry

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditRange(StrEnum):
    HOUR = "1h"
    DAY = "24h"
    WEEK = "7d"


_RANGE_TO_HOURS: dict[AuditRange, int] = {
    AuditRange.HOUR: 1,
    AuditRange.DAY: 24,
    AuditRange.WEEK: 24 * 7,
}


class AuditEntryResponse(BaseModel):
    id: int
    timestamp: datetime
    method: str
    path: str
    server_id: int | None
    status_code: int
    user: str | None
    ip: str | None


class AuditListResponse(BaseModel):
    entries: list[AuditEntryResponse]


@router.get("", response_model=AuditListResponse)
async def list_audit(
    range: AuditRange = Query(default=AuditRange.DAY),
    server_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, description="Сколько записей пропустить (для пагинации)"),
    session: AsyncSession = Depends(get_session),
) -> AuditListResponse:
    """Последние записи аудита. payload не возвращаем в листинге — он может быть большим."""
    cutoff = datetime.now() - timedelta(hours=_RANGE_TO_HOURS[range])
    statement = (
        select(AuditEntry)
        .where(AuditEntry.timestamp >= cutoff)
        .order_by(AuditEntry.timestamp.desc())
        .offset(offset)
        .limit(limit)
    )
    if server_id is not None:
        statement = statement.where(AuditEntry.server_id == server_id)
    result = await session.execute(statement)
    entries = [
        AuditEntryResponse(
            id=entry.id or 0,
            timestamp=entry.timestamp,
            method=entry.method,
            path=entry.path,
            server_id=entry.server_id,
            status_code=entry.status_code,
            user=entry.user,
            ip=entry.ip,
        )
        for entry in result.scalars().all()
    ]
    return AuditListResponse(entries=entries)
