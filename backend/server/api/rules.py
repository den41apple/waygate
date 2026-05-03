from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.db import get_session
from server.models import DnsRule, RoutingRule, Server
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import (
    ApplyDnsRequest,
    ApplyRulesRequest,
    ApplyRulesResponse,
    IpsetApplyRequest,
    RoutingScope,
)
from shared.schemas import DnsRule as AgentDnsRule
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
    scope: RoutingScope = Field(default=RoutingScope.HOST, description="Где применять (host|container)")
    scope_target: str | None = Field(
        default=None,
        description="Имя docker-контейнера для scope=container",
    )


class RuleUpdate(BaseModel):
    enabled: bool | None = None
    fwmark: int | None = None
    table_id: int | None = None
    via_interface: str | None = None
    via_gateway: str | None = None
    scope: RoutingScope | None = None
    scope_target: str | None = None


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
    scope: str
    scope_target: str | None


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
        scope=rule.scope,
        scope_target=rule.scope_target,
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
        scope=request.scope.value,
        scope_target=request.scope_target,
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
    for field, raw_value in update_data.items():
        # RoutingScope enum → строка для SQLModel-колонки `scope`.
        new_value = raw_value.value if isinstance(raw_value, RoutingScope) else raw_value
        setattr(rule, field, new_value)
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
    """Берёт всё содержимое таблицы routing_rule для server_id и шлёт на агент.

    Перед применением routing-правил автоматически вызывается `/v1/dns/apply` —
    это создаёт ipset'ы под dnsmasq, на которые потом ссылаются routing-правила
    DNS-направлений. Без этого `iptables -m set --match-set <name>` падает с
    `Set <name> doesn't exist`. GeoIP-ipset'ы создаются вручную через `/geoip/lists/{id}/sync`
    — здесь не дёргаем (миллионы IPv4, тяжёлая операция).
    """
    server = await _get_server(server_id=server_id, session=session)

    # 1. DNS-prereq: пушим dnsmasq-config + создаём пустые ipset'ы.
    #
    # dnsmasq НЕ создаёт ipset'ы автоматически — он только пишет резолвенные IP в
    # существующие set'ы. Если ipset не создан заранее, то iptables-правило
    # `--match-set <name>` падает с `Set <name> doesn't exist`. Поэтому для каждого
    # DNS-правила здесь дёргаем `/v1/ipset/apply` с пустыми cidrs — agent создаёт
    # ipset через `ipset create -exist`. dnsmasq при следующем DNS-запросе на
    # домен из правила сам начнёт писать в него IP'шники.
    #
    # Долгосрочный фикс — встроить `ipset create -exist` прямо в agent/dns.py,
    # но это требует релиза нового wheel'а агента (см. BACKLOG #15).
    dns_result = await session.execute(
        select(DnsRule).where(DnsRule.server_id == server_id, DnsRule.enabled == True),  # noqa: E712
    )
    dns_rules = dns_result.scalars().all()
    client = AgentClient(host=server.host, port=server.port, token=server.token)
    if dns_rules:
        try:
            for rule in dns_rules:
                try:
                    await client.apply_custom_ipset(
                        request=IpsetApplyRequest(name=rule.ipset_name, cidrs=[]),
                    )
                except AgentClientError as exc:
                    # Идемпотентный bug в agent/ipset.py: tmp_name создаётся с
                    # `hashsize 4096 maxelem 1000000`, целевой — без; после swap
                    # параметры расходятся, повторный `create -exist` падает с
                    # "Set cannot be created: set with the same name already
                    # exists". Set всё равно есть и пригоден — игнорируем.
                    # Долгосрочный фикс — BACKLOG #16.
                    if "already exists" not in str(exc):
                        raise
                    logger.debug("apply_rules: pre-create ipset {} уже есть, продолжаю", rule.ipset_name)
            await client.apply_dns(
                request=ApplyDnsRequest(
                    rules=[
                        AgentDnsRule(name=rule.name, domains=list(rule.domains), ipset_name=rule.ipset_name)
                        for rule in dns_rules
                    ],
                ),
            )
        except AgentUnreachable as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"агент недоступен: {exc}") from exc
        except AgentClientError as exc:
            logger.error("apply_rules: pre-apply dns упал: {}", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"pre-apply dns упал: {exc}",
            ) from exc

    # 2. Routing-правила.
    result = await session.execute(select(RoutingRule).where(RoutingRule.server_id == server_id))
    rules = result.scalars().all()
    request = ApplyRulesRequest(
        rules=[
            AgentRoutingRule(
                # `--` — плейсхолдер для DNS/IPset правил (см. directions.py::_materialize_rules).
                # Старые версии агента ждут CountryCode по `^[A-Za-z]{2}$` — отправляем
                # `ZZ` (ISO 3166-1 reserved code для unknown country); агент `country` вообще
                # не использует, это метаданные. Новые версии агента примут и None.
                country=rule.country if rule.country and rule.country != "--" else "ZZ",
                ipset_name=rule.ipset_name,
                fwmark=rule.fwmark,
                table_id=rule.table_id,
                via_interface=rule.via_interface,
                via_gateway=rule.via_gateway,
                enabled=rule.enabled,
                scope=RoutingScope(rule.scope),
                scope_target=rule.scope_target,
            )
            for rule in rules
        ],
    )
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
