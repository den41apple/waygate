import re
from dataclasses import dataclass

from loguru import logger

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import ApplyRulesResponse, RoutingRule

_IPTABLES_MARK_RE = re.compile(
    r"-A\s+PREROUTING\s+-m\s+set\s+--match-set\s+(?P<ipset>\S+)\s+dst\s+-j\s+MARK"
    r"\s+--set-x?mark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)(?:/\S+)?",
)

_IP_RULE_RE = re.compile(
    r"^\d+:\s+from\s+all\s+fwmark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)\s+lookup\s+(?P<table>\d+)",
)

_IP_ROUTE_DEFAULT_RE = re.compile(r"^default\s+via\s+(?P<gateway>\S+)\s+dev\s+(?P<dev>\S+)")


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
    gateway: str
    interface: str


def _parse_mark(value: str) -> int:
    return int(value, 16) if value.startswith("0x") else int(value)


async def _read_iptables_marks() -> dict[str, _ActiveMark]:
    output = await run_command(["iptables", "-t", "mangle", "-S", "PREROUTING"])
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


async def _read_ip_rules() -> dict[int, _ActiveIpRule]:
    output = await run_command(["ip", "rule", "show"])
    rules: dict[int, _ActiveIpRule] = {}
    for line in output.splitlines():
        match = _IP_RULE_RE.match(line)
        if match is None:
            continue
        fwmark = _parse_mark(match.group("mark"))
        rules[fwmark] = _ActiveIpRule(fwmark=fwmark, table_id=int(match.group("table")))
    return rules


async def _read_default_route(*, table_id: int) -> _ActiveRoute | None:
    output = await run_command(["ip", "route", "show", "table", str(table_id)], check=False)
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


async def _add_iptables_mark(*, ipset_name: str, fwmark: int) -> None:
    await run_command(
        [
            "iptables",
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


async def _remove_iptables_mark(*, ipset_name: str, fwmark: int) -> None:
    await run_command(
        [
            "iptables",
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


async def _add_ip_rule(*, fwmark: int, table_id: int) -> None:
    await run_command(["ip", "rule", "add", "fwmark", str(fwmark), "table", str(table_id)])


async def _remove_ip_rule(*, fwmark: int, table_id: int) -> None:
    await run_command(["ip", "rule", "del", "fwmark", str(fwmark), "table", str(table_id)])


async def _replace_default_route(*, gateway: str, interface: str, table_id: int) -> None:
    # ip route replace атомарно создаёт или обновляет default-маршрут в таблице
    await run_command(
        [
            "ip",
            "route",
            "replace",
            "default",
            "via",
            gateway,
            "dev",
            interface,
            "table",
            str(table_id),
        ],
    )


async def _delete_default_route(*, table_id: int) -> None:
    await run_command(["ip", "route", "del", "default", "table", str(table_id)], check=False)


async def _ensure_mark(*, rule: RoutingRule, current: _ActiveMark | None) -> bool:
    if current is not None and current.fwmark == rule.fwmark:
        return False
    if current is not None:
        await _remove_iptables_mark(ipset_name=current.ipset_name, fwmark=current.fwmark)
    await _add_iptables_mark(ipset_name=rule.ipset_name, fwmark=rule.fwmark)
    return True


async def _ensure_ip_rule(*, rule: RoutingRule, current: _ActiveIpRule | None) -> bool:
    if current is not None and current.table_id == rule.table_id:
        return False
    if current is not None:
        await _remove_ip_rule(fwmark=current.fwmark, table_id=current.table_id)
    await _add_ip_rule(fwmark=rule.fwmark, table_id=rule.table_id)
    return True


async def _ensure_default_route(*, rule: RoutingRule) -> bool:
    current = await _read_default_route(table_id=rule.table_id)
    if current is not None and current.gateway == rule.via_gateway and current.interface == rule.via_interface:
        return False
    await _replace_default_route(
        gateway=rule.via_gateway,
        interface=rule.via_interface,
        table_id=rule.table_id,
    )
    return True


async def _remove_orphans(
    *,
    current_marks: dict[str, _ActiveMark],
    current_ip_rules: dict[int, _ActiveIpRule],
    desired_by_ipset: dict[str, RoutingRule],
    desired_fwmarks: set[int],
    desired_tables: set[int],
    errors: list[str],
) -> None:
    """In-place cleanup: удаляет из current_* всё, чего нет в желаемом."""
    for ipset_name, mark in list(current_marks.items()):
        if ipset_name in desired_by_ipset:
            continue
        try:
            await _remove_iptables_mark(ipset_name=ipset_name, fwmark=mark.fwmark)
            current_marks.pop(ipset_name, None)
        except CommandError as exc:
            errors.append(f"remove orphan mark {ipset_name}: {exc}")

    orphan_tables: set[int] = set()
    for fwmark, ip_rule in list(current_ip_rules.items()):
        if fwmark in desired_fwmarks:
            continue
        try:
            await _remove_ip_rule(fwmark=fwmark, table_id=ip_rule.table_id)
            current_ip_rules.pop(fwmark, None)
            orphan_tables.add(ip_rule.table_id)
        except CommandError as exc:
            errors.append(f"remove orphan ip rule fwmark={fwmark}: {exc}")

    for table_id in orphan_tables - desired_tables:
        try:
            await _delete_default_route(table_id=table_id)
        except CommandError as exc:
            errors.append(f"remove orphan route table={table_id}: {exc}")


async def _read_state(*, errors: list[str]) -> tuple[dict[str, _ActiveMark], dict[int, _ActiveIpRule]]:
    try:
        marks = await _read_iptables_marks()
    except CommandError as exc:
        errors.append(f"read iptables marks: {exc}")
        marks = {}
    try:
        ip_rules = await _read_ip_rules()
    except CommandError as exc:
        errors.append(f"read ip rules: {exc}")
        ip_rules = {}
    return marks, ip_rules


async def apply_rules(*, rules: list[RoutingRule]) -> ApplyRulesResponse:
    """Применяет правила маршрутизации идемпотентно: diff текущего и желаемого.

    1. Считываем текущее состояние iptables mangle PREROUTING, ip rule, ip route.
    2. Удаляем компоненты, которых нет в желаемом списке (orphan-cleanup).
    3. Для каждого желаемого правила добавляем/заменяем недостающие компоненты.
    4. applied = правил, где хотя бы один компонент изменён; skipped = всё совпало.
    """
    desired = [rule for rule in rules if rule.enabled]
    desired_by_ipset = {rule.ipset_name: rule for rule in desired}
    desired_fwmarks = {rule.fwmark for rule in desired}
    desired_tables = {rule.table_id for rule in desired}
    errors: list[str] = []

    current_marks, current_ip_rules = await _read_state(errors=errors)
    await _remove_orphans(
        current_marks=current_marks,
        current_ip_rules=current_ip_rules,
        desired_by_ipset=desired_by_ipset,
        desired_fwmarks=desired_fwmarks,
        desired_tables=desired_tables,
        errors=errors,
    )

    applied = 0
    skipped = 0
    for rule in desired:
        try:
            mark_changed = await _ensure_mark(rule=rule, current=current_marks.get(rule.ipset_name))
            rule_changed = await _ensure_ip_rule(rule=rule, current=current_ip_rules.get(rule.fwmark))
            route_changed = await _ensure_default_route(rule=rule)
        except CommandError as exc:
            errors.append(f"apply {rule.ipset_name}: {exc}")
            continue
        if mark_changed or rule_changed or route_changed:
            applied += 1
        else:
            skipped += 1

    for err in errors:
        logger.warning("apply_rules: {}", err)

    return ApplyRulesResponse(applied=applied, skipped=skipped, errors=errors)
