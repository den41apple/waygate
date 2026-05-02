from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import Server
from shared.schemas import TunnelsResponse

router = APIRouter(prefix="/servers/{server_id}/tunnels", tags=["tunnels"])


@router.get("", response_model=TunnelsResponse)
async def get_tunnels(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> TunnelsResponse:
    """Проксирует /v1/tunnels агента — отдаёт live-список пиров с rx/tx/handshake."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        return await client.tunnels()
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("get_tunnels: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
