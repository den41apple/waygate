"""CRUD для маршрутных направлений (RoutingDirection).

Направление = «трафик из {GeoIP-зон, DNS-правил, IPset-групп} через VPN-клиента
X». В DB направление — это header (`RoutingDirection`) + N child-правил
(`RoutingRule` с одинаковым `direction_id`/`fwmark`/`table_id`/`via_*`).
Каждое child-правило отвечает за один источник трафика — у каждого свой
`ipset_name` (резолвится из refs):

- GeoList → `geoip-<country>-v4` (стандартное имя из GeoIpTab).
- DnsRule → `rule.ipset_name` (то имя, что задал user).
- IpsetGroup → `group.name` (явное имя из формы Custom IPset).

При apply агент получает плоский список RoutingRule (старый interface
`/v1/rules/apply`) — никаких изменений на стороне agent'а не требуется.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete as sqlmodel_delete
from sqlmodel import select

from server.db import get_session
from server.models import (
    AwgClient,
    DnsRule,
    GeoList,
    IpsetGroup,
    RoutingDirection,
    RoutingRule,
    Server,
)
from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager
from shared.schemas import (
    DirectionCreate,
    DirectionListResponse,
    DirectionResponse,
    DirectionUpdate,
    RoutingScope,
)

router = APIRouter(prefix="/servers/{server_id}/directions", tags=["directions"])


# ---------- Резолверы ipset-имён ----------


async def _resolve_ipset_for_geo(
    *,
    geo_list_ids: list[int],
    session: AsyncSession,
) -> dict[int, tuple[str, str]]:
    """{geo_list_id → (ipset_name, country)} — для country поле в RoutingRule."""
    if not geo_list_ids:
        return {}
    result = await session.execute(select(GeoList).where(GeoList.id.in_(geo_list_ids)))
    return {
        item.id: (f"geoip-{item.country.lower()}-v4", item.country)
        for item in result.scalars().all()
        if item.id is not None
    }


async def _resolve_ipset_for_dns(
    *,
    server_id: int,
    dns_rule_ids: list[int],
    session: AsyncSession,
) -> dict[int, str]:
    if not dns_rule_ids:
        return {}
    result = await session.execute(
        select(DnsRule).where(DnsRule.id.in_(dns_rule_ids), DnsRule.server_id == server_id),
    )
    return {rule.id: rule.ipset_name for rule in result.scalars().all() if rule.id is not None}


async def _resolve_ipset_for_groups(
    *,
    server_id: int,
    group_ids: list[int],
    session: AsyncSession,
) -> dict[int, str]:
    if not group_ids:
        return {}
    result = await session.execute(
        select(IpsetGroup).where(IpsetGroup.id.in_(group_ids), IpsetGroup.server_id == server_id),
    )
    return {group.id: group.name for group in result.scalars().all() if group.id is not None}


# ---------- Helpers ----------


async def _load_server(*, server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"server id={server_id} не найден")
    return server


async def _load_direction(
    *,
    server_id: int,
    direction_id: int,
    session: AsyncSession,
) -> RoutingDirection:
    direction = await session.get(RoutingDirection, direction_id)
    if direction is None or direction.server_id != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"direction id={direction_id} не найден на server_id={server_id}",
        )
    return direction


async def _validate_awg_client(
    *,
    server_id: int,
    awg_client_id: int | None,
    session: AsyncSession,
) -> None:
    if awg_client_id is None:
        return
    awg_client = await session.get(AwgClient, awg_client_id)
    if awg_client is None or awg_client.server_id != server_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"awg_client id={awg_client_id} не найден на server_id={server_id}",
        )


async def _next_fwmark_and_table(*, server_id: int, session: AsyncSession) -> tuple[int, int]:
    """Auto-generate fwmark и table_id как `max + 1` среди direction'ов сервера."""
    result = await session.execute(
        select(RoutingDirection).where(RoutingDirection.server_id == server_id),
    )
    directions = result.scalars().all()
    if not directions:
        return 1, 100
    next_fwmark = max(direction.fwmark for direction in directions) + 1
    next_table = max(99, *(direction.table_id for direction in directions)) + 1
    return next_fwmark, next_table


async def _materialize_rules(
    *,
    direction: RoutingDirection,
    geo_list_ids: list[int],
    dns_rule_ids: list[int],
    ipset_group_ids: list[int],
    session: AsyncSession,
) -> list[RoutingRule]:
    """Создаёт N RoutingRule для каждого ref. Каждое правило с одним fwmark/table_id."""
    geo_map = await _resolve_ipset_for_geo(geo_list_ids=geo_list_ids, session=session)
    dns_map = await _resolve_ipset_for_dns(
        server_id=direction.server_id,
        dns_rule_ids=dns_rule_ids,
        session=session,
    )
    group_map = await _resolve_ipset_for_groups(
        server_id=direction.server_id,
        group_ids=ipset_group_ids,
        session=session,
    )

    rules: list[RoutingRule] = []
    for ipset_name, country in geo_map.values():
        rules.append(
            RoutingRule(
                server_id=direction.server_id,
                direction_id=direction.id,
                country=country,
                ipset_name=ipset_name,
                fwmark=direction.fwmark,
                table_id=direction.table_id,
                via_interface=direction.via_interface,
                via_gateway=direction.via_gateway,
                enabled=direction.enabled,
                scope=direction.scope,
                scope_target=direction.scope_target,
            ),
        )
    for ipset_name in dns_map.values():
        rules.append(
            RoutingRule(
                server_id=direction.server_id,
                direction_id=direction.id,
                country="--",
                ipset_name=ipset_name,
                fwmark=direction.fwmark,
                table_id=direction.table_id,
                via_interface=direction.via_interface,
                via_gateway=direction.via_gateway,
                enabled=direction.enabled,
                scope=direction.scope,
                scope_target=direction.scope_target,
            ),
        )
    for ipset_name in group_map.values():
        rules.append(
            RoutingRule(
                server_id=direction.server_id,
                direction_id=direction.id,
                country="--",
                ipset_name=ipset_name,
                fwmark=direction.fwmark,
                table_id=direction.table_id,
                via_interface=direction.via_interface,
                via_gateway=direction.via_gateway,
                enabled=direction.enabled,
                scope=direction.scope,
                scope_target=direction.scope_target,
            ),
        )

    for rule in rules:
        session.add(rule)
    return rules


def _to_response(
    *,
    direction: RoutingDirection,
    geo_list_ids: list[int],
    dns_rule_ids: list[int],
    ipset_group_ids: list[int],
) -> DirectionResponse:
    if direction.id is None:
        raise RuntimeError("RoutingDirection.id None после persist")
    return DirectionResponse(
        id=direction.id,
        server_id=direction.server_id,
        awg_client_id=direction.awg_client_id,
        name=direction.name,
        fwmark=direction.fwmark,
        table_id=direction.table_id,
        via_interface=direction.via_interface,
        via_gateway=direction.via_gateway,
        scope=direction.scope,
        scope_target=direction.scope_target,
        enabled=direction.enabled,
        geo_list_ids=geo_list_ids,
        dns_rule_ids=dns_rule_ids,
        ipset_group_ids=ipset_group_ids,
        created_at=direction.created_at,
        updated_at=direction.updated_at,
    )


async def _collect_refs(
    *,
    direction_id: int,
    session: AsyncSession,
) -> tuple[list[int], list[int], list[int]]:
    """Из child-правил восстанавливаем какие geo/dns/ipset_group участвовали.

    Делаем reverse-lookup по `ipset_name`:
    - `geoip-<cc>-v4` → находим GeoList с country=<cc>.
    - matched DnsRule.ipset_name → DnsRule.id.
    - matched IpsetGroup.name → IpsetGroup.id.
    """
    rules_result = await session.execute(
        select(RoutingRule).where(RoutingRule.direction_id == direction_id),
    )
    rules = rules_result.scalars().all()
    if not rules:
        return [], [], []

    server_id = rules[0].server_id
    ipsets = {rule.ipset_name for rule in rules}

    geo_result = await session.execute(select(GeoList))
    geo_ids = [
        geo.id
        for geo in geo_result.scalars().all()
        if geo.id is not None and f"geoip-{geo.country.lower()}-v4" in ipsets
    ]

    dns_result = await session.execute(select(DnsRule).where(DnsRule.server_id == server_id))
    dns_ids = [rule.id for rule in dns_result.scalars().all() if rule.id is not None and rule.ipset_name in ipsets]

    group_result = await session.execute(
        select(IpsetGroup).where(IpsetGroup.server_id == server_id),
    )
    group_ids = [group.id for group in group_result.scalars().all() if group.id is not None and group.name in ipsets]

    return geo_ids, dns_ids, group_ids


async def _broadcast(*, type: EventType, server_id: int, payload: dict[str, object]) -> None:
    await get_manager().broadcast(
        event=WsEvent(
            type=type,
            server_id=server_id,
            payload=payload,
            timestamp=datetime.now(tz=UTC),
        ),
    )


# ---------- Endpoints ----------


@router.get("", response_model=DirectionListResponse)
async def list_directions(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> DirectionListResponse:
    await _load_server(server_id=server_id, session=session)
    result = await session.execute(
        select(RoutingDirection).where(RoutingDirection.server_id == server_id).order_by(RoutingDirection.id),
    )
    directions = result.scalars().all()
    out: list[DirectionResponse] = []
    for direction in directions:
        if direction.id is None:
            continue
        geo_ids, dns_ids, group_ids = await _collect_refs(
            direction_id=direction.id,
            session=session,
        )
        out.append(
            _to_response(
                direction=direction,
                geo_list_ids=geo_ids,
                dns_rule_ids=dns_ids,
                ipset_group_ids=group_ids,
            ),
        )
    return DirectionListResponse(directions=out)


@router.post("", response_model=DirectionResponse, status_code=status.HTTP_201_CREATED)
async def create_direction(
    server_id: int,
    request: DirectionCreate,
    session: AsyncSession = Depends(get_session),
) -> DirectionResponse:
    await _load_server(server_id=server_id, session=session)
    await _validate_awg_client(
        server_id=server_id,
        awg_client_id=request.awg_client_id,
        session=session,
    )

    fwmark, table_id = await _next_fwmark_and_table(server_id=server_id, session=session)
    direction = RoutingDirection(
        server_id=server_id,
        awg_client_id=request.awg_client_id,
        name=request.name,
        fwmark=fwmark,
        table_id=table_id,
        via_interface=request.via_interface,
        via_gateway=request.via_gateway,
        scope=request.scope.value,
        scope_target=request.scope_target,
        enabled=request.enabled,
    )
    session.add(direction)
    await session.flush()  # получаем direction.id для materialize'а

    await _materialize_rules(
        direction=direction,
        geo_list_ids=request.geo_list_ids,
        dns_rule_ids=request.dns_rule_ids,
        ipset_group_ids=request.ipset_group_ids,
        session=session,
    )
    await session.commit()
    await session.refresh(direction)

    await _broadcast(
        type=EventType.DIRECTION_CREATED,
        server_id=server_id,
        payload={"direction_id": direction.id, "name": direction.name},
    )

    return _to_response(
        direction=direction,
        geo_list_ids=list(request.geo_list_ids),
        dns_rule_ids=list(request.dns_rule_ids),
        ipset_group_ids=list(request.ipset_group_ids),
    )


@router.patch("/{direction_id}", response_model=DirectionResponse)
async def update_direction(
    server_id: int,
    direction_id: int,
    request: DirectionUpdate,
    session: AsyncSession = Depends(get_session),
) -> DirectionResponse:
    direction = await _load_direction(
        server_id=server_id,
        direction_id=direction_id,
        session=session,
    )
    if request.awg_client_id is not None:
        await _validate_awg_client(
            server_id=server_id,
            awg_client_id=request.awg_client_id,
            session=session,
        )

    payload = request.model_dump(exclude_unset=True)
    refs_changed = any(key in payload for key in ("geo_list_ids", "dns_rule_ids", "ipset_group_ids"))
    fields_changed = False
    for field, value in payload.items():
        if field in ("geo_list_ids", "dns_rule_ids", "ipset_group_ids"):
            continue
        new_value = value.value if isinstance(value, RoutingScope) else value
        setattr(direction, field, new_value)
        fields_changed = True

    if fields_changed:
        direction.updated_at = datetime.now()

    # Если изменились child-refs или поля, влияющие на child'ов — перематериализуем
    # дочерние RoutingRule полностью. Это проще чем diff'ать.
    must_rebuild_children = refs_changed or any(
        key in payload
        for key in (
            "via_interface",
            "via_gateway",
            "scope",
            "scope_target",
            "enabled",
        )
    )
    if must_rebuild_children:
        # Восстанавливаем текущие refs если в payload их нет
        current_geo, current_dns, current_groups = await _collect_refs(
            direction_id=direction_id,
            session=session,
        )
        geo_ids = payload.get("geo_list_ids", current_geo)
        dns_ids = payload.get("dns_rule_ids", current_dns)
        group_ids = payload.get("ipset_group_ids", current_groups)

        # Удаляем старых child'ов и создаём новых.
        await session.execute(
            sqlmodel_delete(RoutingRule).where(RoutingRule.direction_id == direction_id),
        )
        await _materialize_rules(
            direction=direction,
            geo_list_ids=list(geo_ids),
            dns_rule_ids=list(dns_ids),
            ipset_group_ids=list(group_ids),
            session=session,
        )

    await session.commit()
    await session.refresh(direction)

    geo_ids, dns_ids, group_ids = await _collect_refs(
        direction_id=direction_id,
        session=session,
    )
    await _broadcast(
        type=EventType.DIRECTION_UPDATED,
        server_id=server_id,
        payload={"direction_id": direction_id, "name": direction.name},
    )
    return _to_response(
        direction=direction,
        geo_list_ids=geo_ids,
        dns_rule_ids=dns_ids,
        ipset_group_ids=group_ids,
    )


@router.delete("/{direction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_direction(
    server_id: int,
    direction_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    direction = await _load_direction(
        server_id=server_id,
        direction_id=direction_id,
        session=session,
    )
    # Каскадное удаление дочерних RoutingRule прописано в FK (ondelete=CASCADE).
    await session.delete(direction)
    await session.commit()
    logger.info("direction удалён: id={}", direction_id)
    await _broadcast(
        type=EventType.DIRECTION_DELETED,
        server_id=server_id,
        payload={"direction_id": direction_id},
    )
