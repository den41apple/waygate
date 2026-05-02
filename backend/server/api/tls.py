from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import Server, TlsConfigRow
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import TlsApplyResponse, TlsConfig

router = APIRouter(prefix="/servers/{server_id}/tls", tags=["tls"])


class TlsConfigResponse(BaseModel):
    server_id: int
    config: dict[str, Any] = Field(description="Сериализованный TlsConfig (без секретов)")
    expires_at: datetime | None


def _redact(*, config: dict[str, Any]) -> dict[str, Any]:
    """Убирает чувствительные поля из ответа GET — не возвращаем cert/key/api-key."""
    redacted = dict(config)
    for sensitive in ("cert_pem", "key_pem", "dns_api_key"):
        if sensitive in redacted:
            redacted[sensitive] = "***" if redacted[sensitive] else None
    return redacted


@router.get("", response_model=TlsConfigResponse)
async def get_tls(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> TlsConfigResponse:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    result = await session.execute(select(TlsConfigRow).where(TlsConfigRow.server_id == server_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TLS-конфигурация ещё не задана")
    return TlsConfigResponse(server_id=server_id, config=_redact(config=row.config), expires_at=row.expires_at)


@router.post("", response_model=TlsApplyResponse)
async def apply_tls(
    server_id: int,
    config: TlsConfig,
    session: AsyncSession = Depends(get_session),
) -> TlsApplyResponse:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")

    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        response = await client.apply_tls(config=config)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("apply_tls: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # upsert TlsConfigRow
    existing = await session.execute(select(TlsConfigRow).where(TlsConfigRow.server_id == server_id))
    row = existing.scalar_one_or_none()
    serialized = config.model_dump(mode="json")
    if row is None:
        session.add(TlsConfigRow(server_id=server_id, config=serialized, expires_at=response.expires_at))
    else:
        row.config = serialized
        row.expires_at = response.expires_at
    await session.commit()

    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.TLS_APPLIED,
            server_id=server_id,
            payload={
                "mode": config.mode.value,
                "domains": response.domains,
                "expires_at": response.expires_at.isoformat(),
            },
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return response
