import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.api.servers import ServerResponse
from server.config import settings
from server.db import get_session, get_session_maker
from server.models import Server, ServerStatus
from server.provisioner.registry import get_registry
from server.provisioner.service import run_provision
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager

router = APIRouter(prefix="/servers", tags=["provision"])


class ProvisionRequest(BaseModel):
    host: str = Field(description="DNS-имя или IP сервера")
    ssh_port: int = Field(default=22, description="SSH-порт")
    ssh_user: str = Field(default="root", description="SSH-пользователь")
    ssh_password: str | None = Field(default=None, description="Пароль (либо ssh_private_key)")
    ssh_private_key: str | None = Field(default=None, description="Private key в PEM-формате")
    name: str = Field(description="Человекочитаемое имя")
    region: str | None = Field(default=None)
    agent_port: int | None = Field(default=None, description="Порт агента после установки (default 7743)")

    @model_validator(mode="after")
    def validate_auth(self) -> "ProvisionRequest":
        if not self.ssh_password and not self.ssh_private_key:
            raise ValueError("Нужен ssh_password или ssh_private_key")
        return self


def _to_response(*, server: Server) -> ServerResponse:
    if server.id is None:
        raise RuntimeError("Server.id None после persist")
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


@router.post("/provision", response_model=ServerResponse, status_code=status.HTTP_202_ACCEPTED)
async def provision_server(
    request: ProvisionRequest,
    session: AsyncSession = Depends(get_session),
) -> ServerResponse:
    """Создаёт Server-record со статусом provisioning, запускает background-таску.

    Клиент получает id и сразу подписывается на /provision/stream чтобы видеть лог.
    """
    agent_port = request.agent_port or settings.agent_default_port
    # Upsert по host: если запись с таким IP/DNS уже есть (re-онбординг после
    # ручной чистки агента на target) — переиспользуем её, сбрасываем токен и
    # переводим в PROVISIONING. Связанные сущности (rules/dns/metrics/tls) не
    # трогаем — пользователь, как правило, ожидает их сохранения.
    # `.first()` (а не `.scalar_one_or_none()`) — на проде до этого фикса могли
    # накопиться дубликаты по host; берём самую свежую и продолжаем работать.
    # Чистка дубликатов — через UI/REST DELETE, отдельной операцией.
    existing_result = await session.execute(
        select(Server).where(Server.host == request.host).order_by(desc(Server.id)),
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        existing.token = ""
        existing.name = request.name
        existing.region = request.region
        existing.port = agent_port
        existing.status = ServerStatus.PROVISIONING.value
        existing.version = ""
        existing.awg_containers = []
        server = existing
        logger.info("provision: переиспользую server id={} host={}", server.id, server.host)
    else:
        server = Server(
            host=request.host,
            port=agent_port,
            name=request.name,
            token="",  # будет установлен по итогам провижионинга
            region=request.region,
            status=ServerStatus.PROVISIONING.value,
        )
        session.add(server)
    await session.commit()
    await session.refresh(server)
    if server.id is None:
        raise HTTPException(status_code=500, detail="не удалось создать запись Server")

    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.SERVER_CREATED,
            server_id=server.id,
            payload={"host": server.host, "name": server.name, "status": server.status},
            timestamp=datetime.now(tz=UTC),
        ),
    )

    registry = get_registry()
    job = await registry.create(server_id=server.id)

    job.task = asyncio.create_task(
        run_provision(
            job=job,
            server_id=server.id,
            host=request.host,
            ssh_port=request.ssh_port,
            ssh_user=request.ssh_user,
            ssh_password=request.ssh_password,
            ssh_private_key=request.ssh_private_key,
            agent_port=agent_port,
            session_maker=get_session_maker(),
        ),
    )
    logger.info("provision: запущен server_id={} host={}", server.id, request.host)
    return _to_response(server=server)


def _format_sse(*, payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/{server_id}/provision/stream")
async def provision_stream(server_id: int) -> StreamingResponse:
    """SSE-стрим лога онбординга. Реплеит историю + стримит до finish."""
    job = get_registry().get(server_id=server_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"для server_id={server_id} нет активного провижионинга",
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
