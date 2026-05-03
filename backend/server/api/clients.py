"""Control-plane CRUD для AmneziaWG-клиентов.

Прокси к агенту: создание/удаление контейнера выполняется агентом, control-plane
ведёт mirror в `awg_clients` со зашифрованным конфигом (для возможности отдать
.conf обратно в UI/QR-код, не дёргая target если там сейчас offline).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.auth.secrets import SecretCipherError, decrypt, encrypt
from server.db import get_session
from server.models import AwgClient, AwgClientStatus, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.awg_config import parse_awg_config
from shared.schemas import AwgClientName, CreateAwgClientRequest

router = APIRouter(prefix="/servers/{server_id}/clients", tags=["awg-clients"])


class CreateClientPayload(BaseModel):
    name: AwgClientName = Field(description="Идентификатор клиента (a-z0-9-)")
    country: str | None = Field(default=None, description="ISO-2 код страны VPN")
    config_text: str = Field(description="Полный .conf-файл")


class AwgClientResponse(BaseModel):
    id: int
    server_id: int
    name: str
    container_name: str
    status: str
    country: str | None
    peer_endpoint: str | None
    peer_pubkey: str | None
    interface_address: str | None
    created_at: datetime
    updated_at: datetime


class AwgClientListResponse(BaseModel):
    clients: list[AwgClientResponse]


def _to_response(*, client: AwgClient) -> AwgClientResponse:
    if client.id is None:
        raise RuntimeError("AwgClient.id None после persist")
    return AwgClientResponse(
        id=client.id,
        server_id=client.server_id,
        name=client.name,
        container_name=client.container_name,
        status=client.status,
        country=client.country,
        peer_endpoint=client.peer_endpoint,
        peer_pubkey=client.peer_pubkey,
        interface_address=client.interface_address,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


async def _load_server(*, server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return server


def _agent_client_for(*, server: Server) -> AgentClient:
    return AgentClient(host=server.host, port=server.port, token=server.token)


async def _broadcast(*, type: EventType, server_id: int, payload: dict[str, object]) -> None:
    await get_manager().broadcast(
        event=WsEvent(type=type, server_id=server_id, payload=payload, timestamp=datetime.now(tz=UTC)),
    )


@router.get("", response_model=AwgClientListResponse)
async def list_clients(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> AwgClientListResponse:
    await _load_server(server_id=server_id, session=session)
    result = await session.execute(select(AwgClient).where(AwgClient.server_id == server_id))
    clients = result.scalars().all()
    return AwgClientListResponse(clients=[_to_response(client=client) for client in clients])


@router.post("", response_model=AwgClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    server_id: int,
    payload: CreateClientPayload,
    session: AsyncSession = Depends(get_session),
) -> AwgClientResponse:
    server = await _load_server(server_id=server_id, session=session)

    # Парсим locally — чтобы выдать пользователю осмысленную 400 если конфиг кривой,
    # без round-trip'а на агента.
    try:
        parsed = parse_awg_config(payload.config_text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"невалидный .conf: {exc}") from exc

    # Шифруем для хранения в БД (PrivateKey там).
    config_encrypted = encrypt(plaintext=payload.config_text)

    client_record = AwgClient(
        server_id=server_id,
        name=payload.name,
        container_name=f"waygate-amnezia-client-{payload.name}",
        config_encrypted=config_encrypted,
        peer_endpoint=parsed.peer.endpoint,
        peer_pubkey=parsed.peer.public_key,
        interface_address=parsed.interface.address,
        country=payload.country,
        status=AwgClientStatus.PENDING.value,
    )
    session.add(client_record)
    await session.commit()
    await session.refresh(client_record)

    # Деплой на target. Если упадёт — оставляем PENDING-record в БД, пользователь
    # может удалить через UI и попробовать заново.
    agent = _agent_client_for(server=server)
    try:
        response = await agent.create_client(
            request=CreateAwgClientRequest(name=payload.name, config_text=payload.config_text),
        )
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("create_client: агент вернул ошибку: {}", exc)
        client_record.status = AwgClientStatus.ERROR.value
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    client_record.status = response.client.status.value
    client_record.updated_at = datetime.now()
    await session.commit()

    await _broadcast(
        type=EventType.AWG_CLIENT_CREATED,
        server_id=server_id,
        payload={"name": payload.name, "container_name": client_record.container_name},
    )

    return _to_response(client=client_record)


async def _get_client(*, server_id: int, client_id: int, session: AsyncSession) -> AwgClient:
    client = await session.get(AwgClient, client_id)
    if client is None or client.server_id != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"awg-client id={client_id} не найден на server_id={server_id}",
        )
    return client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    server_id: int,
    client_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    server = await _load_server(server_id=server_id, session=session)
    client = await _get_client(server_id=server_id, client_id=client_id, session=session)

    agent = _agent_client_for(server=server)
    # Best-effort на target: если он offline, всё равно сносим запись из БД.
    try:
        await agent.delete_client(name=client.name)
    except AgentUnreachable as exc:
        logger.warning("delete_client: агент недоступен (продолжаю удалять из БД): {}", exc)
    except AgentClientError as exc:
        logger.warning("delete_client: агент вернул ошибку (продолжаю удалять из БД): {}", exc)

    await session.delete(client)
    await session.commit()

    await _broadcast(
        type=EventType.AWG_CLIENT_DELETED,
        server_id=server_id,
        payload={"name": client.name},
    )


@router.post("/{client_id}/start", response_model=AwgClientResponse)
async def start_client(
    server_id: int,
    client_id: int,
    session: AsyncSession = Depends(get_session),
) -> AwgClientResponse:
    server = await _load_server(server_id=server_id, session=session)
    client = await _get_client(server_id=server_id, client_id=client_id, session=session)
    agent = _agent_client_for(server=server)
    try:
        action = await agent.start_client(name=client.name)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    client.status = action.status.value
    client.updated_at = datetime.now()
    await session.commit()
    await _broadcast(
        type=EventType.AWG_CLIENT_STATUS_CHANGED,
        server_id=server_id,
        payload={"name": client.name, "status": action.status.value},
    )
    return _to_response(client=client)


@router.post("/{client_id}/stop", response_model=AwgClientResponse)
async def stop_client(
    server_id: int,
    client_id: int,
    session: AsyncSession = Depends(get_session),
) -> AwgClientResponse:
    server = await _load_server(server_id=server_id, session=session)
    client = await _get_client(server_id=server_id, client_id=client_id, session=session)
    agent = _agent_client_for(server=server)
    try:
        action = await agent.stop_client(name=client.name)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    client.status = action.status.value
    client.updated_at = datetime.now()
    await session.commit()
    await _broadcast(
        type=EventType.AWG_CLIENT_STATUS_CHANGED,
        server_id=server_id,
        payload={"name": client.name, "status": action.status.value},
    )
    return _to_response(client=client)


@router.get("/{client_id}/config")
async def get_client_config(
    server_id: int,
    client_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Возвращает расшифрованный .conf — для скачивания файла из UI."""
    client = await _get_client(server_id=server_id, client_id=client_id, session=session)
    try:
        plain = decrypt(token=client.config_encrypted)
    except SecretCipherError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="не удалось расшифровать конфиг (изменён SECRET_KEY?)",
        ) from exc
    return Response(
        content=plain,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{client.name}.conf"'},
    )


@router.get("/{client_id}/qr")
async def get_client_qr(
    server_id: int,
    client_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Прокси PNG-байтов QR-кода с агента (генерируется через qrencode)."""
    server = await _load_server(server_id=server_id, session=session)
    client = await _get_client(server_id=server_id, client_id=client_id, session=session)
    agent = _agent_client_for(server=server)
    try:
        png = await agent.get_client_qr(name=client.name)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except AgentClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png")
