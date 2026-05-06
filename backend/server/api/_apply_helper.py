"""Извлечённый из `api/rules.py` apply-flow.

Цель — переиспользовать ту же последовательность (DNS prereq + custom ipset
prereq + apply_rules + WS broadcast) из двух мест:
- HTTP-эндпоинт `POST /servers/{id}/rules/apply` (явный apply от пользователя);
- автоматический re-apply в `tasks/healthcheck.py` при OFFLINE→ONLINE с непустыми
  `last_apply_errors` от агента.
"""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.agent_client import AgentClient
from server.models import DnsRule, IpsetGroup, RoutingRule, Server
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


async def run_full_apply(
    *,
    server: Server,
    session: AsyncSession,
    client: AgentClient,
) -> ApplyRulesResponse:
    """Полный apply-цикл: DNS-prereq → custom ipset prereq → apply_rules → WS event.

    Кидает `AgentUnreachable` / `AgentClientError` (caller'ы оборачивают как им нужно —
    HTTP-эндпоинт в 502, healthcheck просто логирует).
    """
    if server.id is None:
        # Невозможный кейс — server без id не сохранён в БД, но typing требует guard'а.
        msg = "server без id — не сохранён в БД"
        raise ValueError(msg)
    server_id = server.id

    # 1. DNS-prereq: пушим dnsmasq-config. Сами ipset'ы создаёт agent внутри
    # `apply_dns` (см. agent/dns.py::_ensure_dual_family_ipsets).
    dns_result = await session.execute(
        select(DnsRule).where(DnsRule.server_id == server_id, DnsRule.enabled == True),  # noqa: E712
    )
    dns_rules = dns_result.scalars().all()
    if dns_rules:
        await client.apply_dns(
            request=ApplyDnsRequest(
                rules=[
                    AgentDnsRule(name=rule.name, domains=list(rule.domains), ipset_name=rule.ipset_name)
                    for rule in dns_rules
                ],
            ),
        )

    # 2. Custom IPset prereq: пушим CIDR'ы каждой IpsetGroup, чтобы
    # `iptables --match-set <name>` не падал с "Set <name> doesn't exist".
    ipset_groups_result = await session.execute(
        select(IpsetGroup).where(IpsetGroup.server_id == server_id),
    )
    ipset_groups = ipset_groups_result.scalars().all()
    for group in ipset_groups:
        await client.apply_custom_ipset(
            request=IpsetApplyRequest(name=group.name, cidrs=list(group.cidrs)),
        )

    # 3. Routing-правила.
    rules_result = await session.execute(select(RoutingRule).where(RoutingRule.server_id == server_id))
    rules = rules_result.scalars().all()
    request = ApplyRulesRequest(
        rules=[
            AgentRoutingRule(
                country=rule.country if rule.country and rule.country != "--" else "ZZ",
                ipset_name=rule.ipset_name if rule.ipset_name else None,
                fwmark=rule.fwmark,
                table_id=rule.table_id,
                via_interface=rule.via_interface,
                via_gateway=rule.via_gateway,
                enabled=rule.enabled,
                scope=RoutingScope(rule.scope),
                scope_target=rule.scope_target,
                is_default_egress=rule.is_default_egress,
            )
            for rule in rules
        ],
    )
    response = await client.apply_rules(request=request)
    if response.errors:
        logger.warning(
            "apply_rules: server={} agent вернул {} ошибок: {}",
            server.host,
            len(response.errors),
            response.errors,
        )

    await get_manager().broadcast(
        event=WsEvent(
            type=EventType.RULE_APPLIED,
            server_id=server_id,
            payload={"applied": response.applied, "skipped": response.skipped, "errors": response.errors},
            timestamp=datetime.now(tz=UTC),
        ),
    )
    return response
