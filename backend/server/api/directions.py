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
    DirectionSource,
    DirectionSourceType,
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
    """{geo_list_id → (ipset_name, country)} — для country поле в RoutingRule.

    Имя — **логическое** (`geoip-ru`, без family-суффикса). Agent при apply
    добавит `-v4`/`-v6` в зависимости от стека (см. routing.py::_ipset_name_for).
    Раньше тут было `geoip-ru-v4`, но это конфликтовало с dual-family схемой:
    routing.py добавлял ещё `-v4` поверх → искал `geoip-ru-v4-v4`.
    """
    if not geo_list_ids:
        return {}
    result = await session.execute(select(GeoList).where(GeoList.id.in_(geo_list_ids)))
    return {
        item.id: (f"geoip-{item.country.lower()}", item.country)
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


async def _persist_sources(
    *,
    direction_id: int,
    geo_list_ids: list[int],
    dns_rule_ids: list[int],
    ipset_group_ids: list[int],
    session: AsyncSession,
) -> None:
    """Записывает выбранные источники в pivot-таблицу `direction_sources`.

    Это типизированная замена reverse-lookup'у через `RoutingRule.ipset_name`.
    Источники остаются единственным источником истины (см. `_collect_refs`),
    а child-RoutingRule'ы — только denormalized cache для агента.
    """
    for geo_id in geo_list_ids:
        session.add(
            DirectionSource(
                direction_id=direction_id,
                source_type=DirectionSourceType.GEO_LIST.value,
                source_id=geo_id,
            ),
        )
    for dns_id in dns_rule_ids:
        session.add(
            DirectionSource(
                direction_id=direction_id,
                source_type=DirectionSourceType.DNS_RULE.value,
                source_id=dns_id,
            ),
        )
    for group_id in ipset_group_ids:
        session.add(
            DirectionSource(
                direction_id=direction_id,
                source_type=DirectionSourceType.IPSET_GROUP.value,
                source_id=group_id,
            ),
        )


async def _materialize_rules(
    *,
    direction: RoutingDirection,
    geo_list_ids: list[int],
    dns_rule_ids: list[int],
    ipset_group_ids: list[int],
    session: AsyncSession,
) -> list[RoutingRule]:
    """Создаёт N RoutingRule для каждого ref. Каждое правило с одним fwmark/table_id.

    Для catch-all direction'а (`is_default_egress=True`) создаётся ровно один
    sentinel-RoutingRule с пустым `ipset_name` и `is_default_egress=True`.
    Источники (geo/dns/ipset_group) не нужны — agent применяет unconditional
    MARK для пакетов с `mark==0`.
    """
    if direction.is_default_egress:
        sentinel = RoutingRule(
            server_id=direction.server_id,
            direction_id=direction.id,
            country="--",
            ipset_name="",
            fwmark=direction.fwmark,
            table_id=direction.table_id,
            via_interface=direction.via_interface,
            via_gateway=direction.via_gateway,
            enabled=direction.enabled,
            scope=direction.scope,
            scope_target=direction.scope_target,
            is_default_egress=True,
        )
        session.add(sentinel)
        return [sentinel]

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
        is_default_egress=direction.is_default_egress,
        geo_list_ids=geo_list_ids,
        dns_rule_ids=dns_rule_ids,
        ipset_group_ids=ipset_group_ids,
        created_at=direction.created_at,
        updated_at=direction.updated_at,
    )


async def _ensure_unique_default_egress(
    *,
    server_id: int,
    scope: str,
    is_default_egress: bool,
    exclude_direction_id: int | None,
    session: AsyncSession,
) -> None:
    """Бросает 409 если в (server, scope) уже есть другой `is_default_egress=True`."""
    if not is_default_egress:
        return
    query = select(RoutingDirection).where(
        RoutingDirection.server_id == server_id,
        RoutingDirection.scope == scope,
        RoutingDirection.is_default_egress.is_(True),
    )
    existing = (await session.execute(query)).scalars().all()
    for direction in existing:
        if direction.id == exclude_direction_id:
            continue
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"catch-all direction уже существует на server_id={server_id} в scope={scope} "
                f"(direction id={direction.id}, name='{direction.name}')"
            ),
        )


async def _collect_refs(
    *,
    direction_id: int,
    session: AsyncSession,
) -> tuple[list[int], list[int], list[int]]:
    """Достаёт geo/dns/ipset_group IDs напрямую из pivot-таблицы `direction_sources`.

    До B1 здесь был reverse-lookup по `RoutingRule.ipset_name` (string-magic,
    legacy-имена с/без `-v4`-суффикса). Теперь источники хранятся явно,
    типы и id — из `DirectionSourceType` enum'а.
    """
    result = await session.execute(
        select(DirectionSource).where(DirectionSource.direction_id == direction_id),
    )
    sources = result.scalars().all()
    geo_ids: list[int] = []
    dns_ids: list[int] = []
    group_ids: list[int] = []
    for source in sources:
        if source.source_type == DirectionSourceType.GEO_LIST.value:
            geo_ids.append(source.source_id)
        elif source.source_type == DirectionSourceType.DNS_RULE.value:
            dns_ids.append(source.source_id)
        elif source.source_type == DirectionSourceType.IPSET_GROUP.value:
            group_ids.append(source.source_id)
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
    await _ensure_unique_default_egress(
        server_id=server_id,
        scope=request.scope.value,
        is_default_egress=request.is_default_egress,
        exclude_direction_id=None,
        session=session,
    )
    if request.is_default_egress and (request.geo_list_ids or request.dns_rule_ids or request.ipset_group_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="catch-all direction несовместим с источниками (geo/dns/ipset_group). Очистите чекбоксы.",
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
        is_default_egress=request.is_default_egress,
    )
    session.add(direction)
    await session.flush()  # получаем direction.id для materialize'а
    assert direction.id is not None  # после flush'а ID гарантированно есть

    await _persist_sources(
        direction_id=direction.id,
        geo_list_ids=list(request.geo_list_ids),
        dns_rule_ids=list(request.dns_rule_ids),
        ipset_group_ids=list(request.ipset_group_ids),
        session=session,
    )
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
    # До mutate'а direction'а — проверим UNIQUE на catch-all'ах. Берём
    # NEW значение если в payload'е, иначе текущее.
    new_scope = (
        payload["scope"].value
        if isinstance(payload.get("scope"), RoutingScope)
        else payload.get("scope", direction.scope)
    )
    new_default_egress = payload.get("is_default_egress", direction.is_default_egress)
    await _ensure_unique_default_egress(
        server_id=server_id,
        scope=new_scope,
        is_default_egress=new_default_egress,
        exclude_direction_id=direction_id,
        session=session,
    )

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
            "is_default_egress",
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

        # Удаляем старых sources + child'ов и создаём новых.
        await session.execute(
            sqlmodel_delete(DirectionSource).where(DirectionSource.direction_id == direction_id),
        )
        await session.execute(
            sqlmodel_delete(RoutingRule).where(RoutingRule.direction_id == direction_id),
        )
        await _persist_sources(
            direction_id=direction_id,
            geo_list_ids=list(geo_ids),
            dns_rule_ids=list(dns_ids),
            ipset_group_ids=list(group_ids),
            session=session,
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
