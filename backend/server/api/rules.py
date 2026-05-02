from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import RoutingRule, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import ApplyRulesRequest, ApplyRulesResponse
from shared.schemas import RoutingRule as AgentRoutingRule

router = APIRouter(prefix="/servers/{server_id}/rules", tags=["rules"])


class RuleCreate(BaseModel):
    country: str = Field(description="Код страны ISO 3166-1 alpha-2")
    ipset_name: str = Field(description="Имя ipset на агенте")
    fwmark: int = Field(description="Метка пакета")
    table_id: int = Field(description="Таблица маршрутизации")
    via_interface: str = Field(description="Исходящий интерфейс на агенте")
    via_gateway: str = Field(description="Шлюз внутри туннеля")
    enabled: bool = Field(default=True)


class RuleUpdate(BaseModel):
    enabled: bool | None = None
    fwmark: int | None = None
    table_id: int | None = None
    via_interface: str | None = None
    via_gateway: str | None = None


class RuleResponse(BaseModel):
    id: int
    server_id: int
    country: str
    ipset_name: str
    fwmark: int
    table_id: int
    via_interface: str
    via_gateway: str
    enabled: bool


class RuleListResponse(BaseModel):
    rules: list[RuleResponse]


def _to_response(*, rule: RoutingRule) -> RuleResponse:
    if rule.id is None:
        raise RuntimeError("RoutingRule.id None после persist")
    return RuleResponse(
        id=rule.id,
        server_id=rule.server_id,
        country=rule.country,
        ipset_name=rule.ipset_name,
        fwmark=rule.fwmark,
        table_id=rule.table_id,
        via_interface=rule.via_interface,
        via_gateway=rule.via_gateway,
        enabled=rule.enabled,
    )


async def _get_server(*, server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return server


@router.get("", response_model=RuleListResponse)
async def list_rules(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> RuleListResponse:
    await _get_server(server_id=server_id, session=session)
    result = await session.execute(
        select(RoutingRule).where(RoutingRule.server_id == server_id).order_by(RoutingRule.id),
    )
    return RuleListResponse(rules=[_to_response(rule=rule) for rule in result.scalars().all()])


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    server_id: int,
    request: RuleCreate,
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    await _get_server(server_id=server_id, session=session)
    rule = RoutingRule(
        server_id=server_id,
        country=request.country,
        ipset_name=request.ipset_name,
        fwmark=request.fwmark,
        table_id=request.table_id,
        via_interface=request.via_interface,
        via_gateway=request.via_gateway,
        enabled=request.enabled,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _to_response(rule=rule)


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    server_id: int,
    rule_id: int,
    request: RuleUpdate,
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    rule = await session.get(RoutingRule, rule_id)
    if rule is None or rule.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"rule id={rule_id} не найден")
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return _to_response(rule=rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    server_id: int,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    rule = await session.get(RoutingRule, rule_id)
    if rule is None or rule.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"rule id={rule_id} не найден")
    await session.delete(rule)
    await session.commit()


@router.post("/apply", response_model=ApplyRulesResponse)
async def apply_rules(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApplyRulesResponse:
    """Берёт всё содержимое таблицы routing_rule для server_id и шлёт на агент."""
    server = await _get_server(server_id=server_id, session=session)
    result = await session.execute(select(RoutingRule).where(RoutingRule.server_id == server_id))
    rules = result.scalars().all()
    request = ApplyRulesRequest(
        rules=[
            AgentRoutingRule(
                country=rule.country,
                ipset_name=rule.ipset_name,
                fwmark=rule.fwmark,
                table_id=rule.table_id,
                via_interface=rule.via_interface,
                via_gateway=rule.via_gateway,
                enabled=rule.enabled,
            )
            for rule in rules
        ],
    )
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        response = await client.apply_rules(request=request)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("apply_rules: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.RULE_APPLIED,
            server_id=server_id,
            payload={"applied": response.applied, "skipped": response.skipped, "errors": response.errors},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return response
