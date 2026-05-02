from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import DnsRule, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import ApplyDnsRequest, ApplyDnsResponse
from shared.schemas import DnsRule as AgentDnsRule

router = APIRouter(prefix="/servers/{server_id}/dns", tags=["dns"])


class DnsRuleCreate(BaseModel):
    name: str = Field(description="Группа доменов, для UI")
    domains: list[str] = Field(description="Домены для подмены резолва в ipset")
    ipset_name: str = Field(description="Куда писать резолвы")
    enabled: bool = Field(default=True)


class DnsRuleUpdate(BaseModel):
    name: str | None = None
    domains: list[str] | None = None
    ipset_name: str | None = None
    enabled: bool | None = None


class DnsRuleResponse(BaseModel):
    id: int
    server_id: int
    name: str
    domains: list[str]
    ipset_name: str
    enabled: bool


class DnsRuleListResponse(BaseModel):
    rules: list[DnsRuleResponse]


def _to_response(*, rule: DnsRule) -> DnsRuleResponse:
    if rule.id is None:
        raise RuntimeError("DnsRule.id None после persist")
    return DnsRuleResponse(
        id=rule.id,
        server_id=rule.server_id,
        name=rule.name,
        domains=list(rule.domains),
        ipset_name=rule.ipset_name,
        enabled=rule.enabled,
    )


async def _get_server(*, server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return server


@router.get("", response_model=DnsRuleListResponse)
async def list_dns_rules(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> DnsRuleListResponse:
    await _get_server(server_id=server_id, session=session)
    result = await session.execute(
        select(DnsRule).where(DnsRule.server_id == server_id).order_by(DnsRule.id),
    )
    return DnsRuleListResponse(rules=[_to_response(rule=rule) for rule in result.scalars().all()])


@router.post("", response_model=DnsRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_dns_rule(
    server_id: int,
    request: DnsRuleCreate,
    session: AsyncSession = Depends(get_session),
) -> DnsRuleResponse:
    await _get_server(server_id=server_id, session=session)
    rule = DnsRule(
        server_id=server_id,
        name=request.name,
        domains=request.domains,
        ipset_name=request.ipset_name,
        enabled=request.enabled,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _to_response(rule=rule)


@router.patch("/{rule_id}", response_model=DnsRuleResponse)
async def update_dns_rule(
    server_id: int,
    rule_id: int,
    request: DnsRuleUpdate,
    session: AsyncSession = Depends(get_session),
) -> DnsRuleResponse:
    rule = await session.get(DnsRule, rule_id)
    if rule is None or rule.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"dns rule id={rule_id} не найден")
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return _to_response(rule=rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dns_rule(
    server_id: int,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    rule = await session.get(DnsRule, rule_id)
    if rule is None or rule.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"dns rule id={rule_id} не найден")
    await session.delete(rule)
    await session.commit()


@router.post("/apply", response_model=ApplyDnsResponse)
async def apply_dns(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApplyDnsResponse:
    server = await _get_server(server_id=server_id, session=session)
    result = await session.execute(
        select(DnsRule).where(DnsRule.server_id == server_id, DnsRule.enabled == True),  # noqa: E712
    )
    rules = result.scalars().all()
    request = ApplyDnsRequest(
        rules=[AgentDnsRule(name=rule.name, domains=list(rule.domains), ipset_name=rule.ipset_name) for rule in rules],
    )
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    try:
        response = await client.apply_dns(request=request)
    except AgentUnreachable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
    except AgentClientError as exc:
        logger.error("apply_dns: агент вернул ошибку: {}", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.DNS_APPLIED,
            server_id=server_id,
            payload={"applied": response.applied, "errors": response.errors},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return response
