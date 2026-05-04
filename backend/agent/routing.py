"""Идемпотентное применение правил маршрутизации.

Поддерживает два scope:
- `host` — iptables/ip rule/ip route на самом target. Применяется напрямую через
  `subprocess`. Это default-поведение, оно работает с rules, у которых scope не
  указан (legacy).
- `container` — те же команды, но **внутри netns** docker-контейнера через
  `nsenter -t <pid> -n`. Используется чтобы маршрутизировать трафик клиентов
  AmneziaWG-server-контейнера через клиентский AWG-туннель (двойной VPN).
  Каждый netns имеет собственный набор iptables/ip rule/ipset — изоляция.

Каждый rule применяется в **двух стеках** — IPv4 и IPv6. dnsmasq пишет
A-записи в `<ipset>-v4`, AAAA — в `<ipset>-v6`; iptables/ip6tables match'ит
свой стек. Без IPv6-стека curl на сайт с AAAA (youtube/google/etc) уходил
мимо VPN.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import ApplyRulesResponse, RoutingRule, RoutingScope


class _IpFamily(StrEnum):
    V4 = "v4"
    V6 = "v6"


@dataclass(frozen=True)
class _FamilyTools:
    """Команды и суффиксы для конкретного IP-стека.

    Внутри agent'а ipset'ы dual-family: для логического имени `dns-youtube`
    физически создаются `dns-youtube-v4` (hash:net family inet) и
    `dns-youtube-v6` (hash:net family inet6). dnsmasq пишет в оба через
    `ipset=/domain/v4set,v6set`-директиву.
    """

    family: _IpFamily
    iptables_cmd: str  # "iptables" или "ip6tables"
    ip_args: tuple[str, ...]  # () для v4, ("-6",) для v6 — между `ip` и подкомандой
    ipset_suffix: str  # "-v4" или "-v6"
    has_gateway: bool  # IPv4-маршрут с gateway, IPv6 — без (просто dev awg-X)


_FAMILY_V4 = _FamilyTools(
    family=_IpFamily.V4,
    iptables_cmd="iptables",
    ip_args=(),
    ipset_suffix="-v4",
    has_gateway=True,
)
_FAMILY_V6 = _FamilyTools(
    family=_IpFamily.V6,
    iptables_cmd="ip6tables",
    ip_args=("-6",),
    ipset_suffix="-v6",
    has_gateway=False,
)
_FAMILIES = (_FAMILY_V4, _FAMILY_V6)


_IPTABLES_MARK_RE = re.compile(
    r"-A\s+PREROUTING\s+-m\s+set\s+--match-set\s+(?P<ipset>\S+)\s+dst\s+-j\s+MARK"
    r"\s+--set-x?mark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)(?:/\S+)?",
)

_IP_RULE_RE = re.compile(
    r"^\d+:\s+from\s+all\s+fwmark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)\s+lookup\s+(?P<table>\d+)",
)

# default-route шаблон: и `default via <gw> dev <iface>` (v4 onlink), и
# `default dev <iface>` (v6 без gateway). gateway optional.
_IP_ROUTE_DEFAULT_RE = re.compile(
    r"^default(?:\s+via\s+(?P<gateway>\S+))?\s+dev\s+(?P<dev>\S+)",
)


@dataclass(frozen=True)
class _ActiveMark:
    """Найденное iptables mangle-правило, маркирующее пакеты ipset → fwmark."""

    ipset_name: str
    fwmark: int


@dataclass(frozen=True)
class _ActiveIpRule:
    """Найденная ip rule fwmark X lookup Y."""

    fwmark: int
    table_id: int


@dataclass(frozen=True)
class _ActiveRoute:
    """Default-маршрут в конкретной таблице."""

    table_id: int
    gateway: str | None  # None для IPv6 (default dev awg-X без via).
    interface: str


@dataclass(frozen=True)
class _ScopeContext:
    """Где исполнять команды. Для host это пустой prefix, для container — nsenter."""

    name: str  # "host" или "container:<имя>" — для логов и errors
    command_prefix: list[str]  # пустой для host, ["nsenter", "-t", str(pid), "-n"] для container


async def _resolve_container_pid(*, container: str) -> int:
    """Получает PID контейнера через `docker inspect` для nsenter -t."""
    output = await run_command(
        ["docker", "inspect", "-f", "{{.State.Pid}}", container],
    )
    pid = int(output.strip())
    if pid <= 0:
        raise CommandError(
            command=["docker", "inspect", container],
            returncode=1,
            stderr=f"контейнер {container!r} не запущен (pid={pid})",
        )
    return pid


async def _build_scope_context(*, scope: RoutingScope, scope_target: str | None) -> _ScopeContext:
    if scope is RoutingScope.HOST:
        return _ScopeContext(name="host", command_prefix=[])
    if scope_target is None:
        raise CommandError(
            command=["scope-resolve"],
            returncode=1,
            stderr="scope=container требует scope_target — имя docker-контейнера",
        )
    pid = await _resolve_container_pid(container=scope_target)
    return _ScopeContext(
        name=f"container:{scope_target}",
        command_prefix=["nsenter", "-t", str(pid), "-n"],
    )


def _parse_mark(value: str) -> int:
    return int(value, 16) if value.startswith("0x") else int(value)


async def _read_iptables_marks(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
) -> dict[str, _ActiveMark]:
    output = await run_command(
        [*ctx.command_prefix, family.iptables_cmd, "-t", "mangle", "-S", "PREROUTING"],
    )
    marks: dict[str, _ActiveMark] = {}
    for line in output.splitlines():
        match = _IPTABLES_MARK_RE.search(line)
        if match is None:
            continue
        ipset_name = match.group("ipset")
        marks[ipset_name] = _ActiveMark(
            ipset_name=ipset_name,
            fwmark=_parse_mark(match.group("mark")),
        )
    return marks


async def _read_ip_rules(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
) -> dict[int, _ActiveIpRule]:
    output = await run_command([*ctx.command_prefix, "ip", *family.ip_args, "rule", "show"])
    rules: dict[int, _ActiveIpRule] = {}
    for line in output.splitlines():
        match = _IP_RULE_RE.match(line)
        if match is None:
            continue
        fwmark = _parse_mark(match.group("mark"))
        rules[fwmark] = _ActiveIpRule(fwmark=fwmark, table_id=int(match.group("table")))
    return rules


async def _read_default_route(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    table_id: int,
) -> _ActiveRoute | None:
    output = await run_command(
        [*ctx.command_prefix, "ip", *family.ip_args, "route", "show", "table", str(table_id)],
        check=False,
    )
    for line in output.splitlines():
        match = _IP_ROUTE_DEFAULT_RE.match(line)
        if match is None:
            continue
        return _ActiveRoute(
            table_id=table_id,
            gateway=match.group("gateway"),
            interface=match.group("dev"),
        )
    return None


async def _add_iptables_mark(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    ipset_name: str,
    fwmark: int,
) -> None:
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "mangle",
            "-A",
            "PREROUTING",
            "-m",
            "set",
            "--match-set",
            ipset_name,
            "dst",
            "-j",
            "MARK",
            "--set-mark",
            str(fwmark),
        ],
    )


async def _remove_iptables_mark(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    ipset_name: str,
    fwmark: int,
) -> None:
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "mangle",
            "-D",
            "PREROUTING",
            "-m",
            "set",
            "--match-set",
            ipset_name,
            "dst",
            "-j",
            "MARK",
            "--set-mark",
            str(fwmark),
        ],
    )


async def _add_ip_rule(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    fwmark: int,
    table_id: int,
) -> None:
    await run_command(
        [*ctx.command_prefix, "ip", *family.ip_args, "rule", "add", "fwmark", str(fwmark), "table", str(table_id)],
    )


async def _remove_ip_rule(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    fwmark: int,
    table_id: int,
) -> None:
    await run_command(
        [*ctx.command_prefix, "ip", *family.ip_args, "rule", "del", "fwmark", str(fwmark), "table", str(table_id)],
    )


async def _replace_default_route(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    gateway: str,
    interface: str,
    table_id: int,
) -> None:
    """Atomic replace default-маршрута в таблице.

    Для IPv4: `ip route replace default via <gw> dev <iface> onlink table <id>`.
    `onlink` критично для AWG-клиентов с `Address = X.Y.Z.W/32` (single-IP без
    подсети) — без флага kernel падает с "Error: Nexthop has invalid gateway".

    Для IPv6: AmneziaWG-туннели обычно point-to-point без явного IPv6-gateway
    (в `.conf` нет AddressV6). Используем `ip -6 route replace default dev <iface>`
    без `via` — kernel сам определяет next-hop через интерфейс.
    """
    cmd = [*ctx.command_prefix, "ip", *family.ip_args, "route", "replace", "default"]
    if family.has_gateway:
        cmd.extend(["via", gateway, "dev", interface, "onlink"])
    else:
        cmd.extend(["dev", interface])
    cmd.extend(["table", str(table_id)])
    await run_command(cmd)


async def _delete_default_route(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    table_id: int,
) -> None:
    await run_command(
        [*ctx.command_prefix, "ip", *family.ip_args, "route", "del", "default", "table", str(table_id)],
        check=False,
    )


def _ipset_name_for(*, rule: RoutingRule, family: _FamilyTools) -> str:
    """Логическое имя `dns-youtube` → физическое `dns-youtube-v4`/`dns-youtube-v6`."""
    return f"{rule.ipset_name}{family.ipset_suffix}"


async def _ensure_mark(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rule: RoutingRule,
    current: _ActiveMark | None,
) -> bool:
    physical_name = _ipset_name_for(rule=rule, family=family)
    if current is not None and current.fwmark == rule.fwmark:
        return False
    if current is not None:
        await _remove_iptables_mark(
            ctx=ctx,
            family=family,
            ipset_name=current.ipset_name,
            fwmark=current.fwmark,
        )
    await _add_iptables_mark(ctx=ctx, family=family, ipset_name=physical_name, fwmark=rule.fwmark)
    return True


async def _ensure_ip_rule(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rule: RoutingRule,
    current: _ActiveIpRule | None,
) -> bool:
    if current is not None and current.table_id == rule.table_id:
        return False
    if current is not None:
        await _remove_ip_rule(
            ctx=ctx,
            family=family,
            fwmark=current.fwmark,
            table_id=current.table_id,
        )
    await _add_ip_rule(ctx=ctx, family=family, fwmark=rule.fwmark, table_id=rule.table_id)
    return True


async def _ensure_default_route(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rule: RoutingRule,
) -> bool:
    current = await _read_default_route(ctx=ctx, family=family, table_id=rule.table_id)
    expected_gateway = rule.via_gateway if family.has_gateway else None
    if current is not None and current.gateway == expected_gateway and current.interface == rule.via_interface:
        return False
    await _replace_default_route(
        ctx=ctx,
        family=family,
        gateway=rule.via_gateway,
        interface=rule.via_interface,
        table_id=rule.table_id,
    )
    return True


async def _remove_orphans(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    current_marks: dict[str, _ActiveMark],
    current_ip_rules: dict[int, _ActiveIpRule],
    desired_physical_ipsets: set[str],
    desired_fwmarks: set[int],
    desired_tables: set[int],
    errors: list[str],
) -> None:
    """In-place cleanup: удаляет из current_* всё, чего нет в желаемом."""
    for ipset_name, mark in list(current_marks.items()):
        if ipset_name in desired_physical_ipsets:
            continue
        try:
            await _remove_iptables_mark(
                ctx=ctx,
                family=family,
                ipset_name=ipset_name,
                fwmark=mark.fwmark,
            )
            current_marks.pop(ipset_name, None)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] remove orphan mark {ipset_name}: {exc}")

    orphan_tables: set[int] = set()
    for fwmark, ip_rule in list(current_ip_rules.items()):
        if fwmark in desired_fwmarks:
            continue
        try:
            await _remove_ip_rule(
                ctx=ctx,
                family=family,
                fwmark=fwmark,
                table_id=ip_rule.table_id,
            )
            current_ip_rules.pop(fwmark, None)
            orphan_tables.add(ip_rule.table_id)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] remove orphan ip rule fwmark={fwmark}: {exc}")

    for table_id in orphan_tables - desired_tables:
        try:
            await _delete_default_route(ctx=ctx, family=family, table_id=table_id)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] remove orphan route table={table_id}: {exc}")


async def _read_state(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    errors: list[str],
) -> tuple[dict[str, _ActiveMark], dict[int, _ActiveIpRule]]:
    try:
        marks = await _read_iptables_marks(ctx=ctx, family=family)
    except CommandError as exc:
        errors.append(f"[{ctx.name}/{family.family}] read iptables marks: {exc}")
        marks = {}
    try:
        ip_rules = await _read_ip_rules(ctx=ctx, family=family)
    except CommandError as exc:
        errors.append(f"[{ctx.name}/{family.family}] read ip rules: {exc}")
        ip_rules = {}
    return marks, ip_rules


async def _apply_rules_in_scope_family(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rules: list[RoutingRule],
    errors: list[str],
) -> tuple[int, int]:
    """Применяет диф для одного scope+family. Возвращает (applied, skipped)."""
    desired_physical_ipsets = {_ipset_name_for(rule=rule, family=family) for rule in rules}
    desired_fwmarks = {rule.fwmark for rule in rules}
    desired_tables = {rule.table_id for rule in rules}

    current_marks, current_ip_rules = await _read_state(ctx=ctx, family=family, errors=errors)
    await _remove_orphans(
        ctx=ctx,
        family=family,
        current_marks=current_marks,
        current_ip_rules=current_ip_rules,
        desired_physical_ipsets=desired_physical_ipsets,
        desired_fwmarks=desired_fwmarks,
        desired_tables=desired_tables,
        errors=errors,
    )

    applied = 0
    skipped = 0
    for rule in rules:
        physical_name = _ipset_name_for(rule=rule, family=family)
        try:
            mark_changed = await _ensure_mark(
                ctx=ctx,
                family=family,
                rule=rule,
                current=current_marks.get(physical_name),
            )
            rule_changed = await _ensure_ip_rule(
                ctx=ctx,
                family=family,
                rule=rule,
                current=current_ip_rules.get(rule.fwmark),
            )
            route_changed = await _ensure_default_route(ctx=ctx, family=family, rule=rule)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] apply {physical_name}: {exc}")
            continue
        if mark_changed or rule_changed or route_changed:
            applied += 1
        else:
            skipped += 1
    return applied, skipped


async def _apply_rules_in_scope(
    *,
    ctx: _ScopeContext,
    rules: list[RoutingRule],
    errors: list[str],
) -> tuple[int, int]:
    """Применяет диф для одного scope обоими стеками (V4 + V6)."""
    total_applied = 0
    total_skipped = 0
    for family in _FAMILIES:
        applied, skipped = await _apply_rules_in_scope_family(
            ctx=ctx,
            family=family,
            rules=rules,
            errors=errors,
        )
        total_applied += applied
        total_skipped += skipped
    return total_applied, total_skipped


def _scope_key(*, rule: RoutingRule) -> tuple[RoutingScope, str | None]:
    """Группирующий ключ — (scope, scope_target)."""
    return (rule.scope, rule.scope_target)


def _group_by_scope(rules: Iterable[RoutingRule]) -> dict[tuple[RoutingScope, str | None], list[RoutingRule]]:
    groups: dict[tuple[RoutingScope, str | None], list[RoutingRule]] = {}
    for rule in rules:
        groups.setdefault(_scope_key(rule=rule), []).append(rule)
    return groups


async def apply_rules(*, rules: list[RoutingRule]) -> ApplyRulesResponse:
    """Применяет правила маршрутизации идемпотентно.

    Группирует правила по (scope, scope_target). Для каждой группы строит
    `_ScopeContext` (host или nsenter в netns container'а) и вызывает один и тот
    же diff-applier с этим контекстом — отдельно для IPv4 и IPv6 стеков.
    """
    desired = [rule for rule in rules if rule.enabled]
    errors: list[str] = []

    total_applied = 0
    total_skipped = 0
    for (scope, target), group_rules in _group_by_scope(desired).items():
        try:
            ctx = await _build_scope_context(scope=scope, scope_target=target)
        except CommandError as exc:
            label = f"container:{target}" if scope is RoutingScope.CONTAINER else "host"
            errors.append(f"[{label}] resolve scope: {exc}")
            continue
        applied, skipped = await _apply_rules_in_scope(
            ctx=ctx,
            rules=group_rules,
            errors=errors,
        )
        total_applied += applied
        total_skipped += skipped

    for err in errors:
        logger.warning("apply_rules: {}", err)

    return ApplyRulesResponse(applied=total_applied, skipped=total_skipped, errors=errors)
