"""Прокси к agent'овскому /v1/containers — для UI выбора `scope_target`.

Используется в модалке создания/редактирования RoutingDirection: когда оператор
выбирает scope=container, ему нужно подставить имя реального docker-контейнера.
Без этого endpoint'а оператор вводил имя руками и ошибки видел только при
Apply (агент возвращал "контейнер X не запущен").
"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import Server
from shared.schemas import ContainerListResponse

router = APIRouter(prefix="/servers/{server_id}/containers", tags=["containers"])


@router.get("", response_model=ContainerListResponse)
async def list_containers(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ContainerListResponse:
    """Список всех docker-контейнеров (running + stopped) на target-сервере."""
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        return await client.list_containers()
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("list_containers: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
