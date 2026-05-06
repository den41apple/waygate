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

import asyncio
import functools
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from loguru import logger

from agent import awg_clients
from agent.config import settings
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
    r"-A\s+(?:PREROUTING|FORWARD|OUTPUT)\s+-m\s+set\s+--match-set\s+(?P<ipset>\S+)\s+dst\s+-j\s+MARK"
    r"\s+--set-x?mark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)(?:/\S+)?",
)

# Для catch-all (`is_default_egress=True`) MARK правило не имеет `--match-set`,
# вместо этого `-m mark --mark 0` (помечаем только пакеты ещё без mark'а).
# Хранится в том же mangle-state-словаре под синтетическим ключом DEFAULT_EGRESS_KEY.
_DEFAULT_EGRESS_MARK_RE = re.compile(
    r"-A\s+(?:PREROUTING|FORWARD|OUTPUT)\s+-m\s+mark\s+--mark\s+0(?:/\S+)?\s+-j\s+MARK"
    r"\s+--set-x?mark\s+(?P<mark>0x[0-9a-fA-F]+|\d+)(?:/\S+)?",
)
_DEFAULT_EGRESS_KEY = "__waygate_default_egress__"

# Цепочки, в которые добавляем `--match-set ... -j MARK`:
# - PREROUTING — для forwarded трафика (telephone → AWG-server → VPN-client).
#   Mark ставится ДО routing decision'а, `ip rule fwmark X table Y` корректно
#   пересматривает out_iface. `addrtype LOCAL bypass` защищает self-traffic
#   (incoming SSH/agent/handshake) от случайного marking'а.
# OUTPUT chain МЫ НЕ ИСПОЛЬЗУЕМ — там local-originated (curl с самого VM,
# dockerd, apt-get). Mark в OUTPUT работает в простых случаях, но фундаментально
# хрупок: socket bind'ится к eth0_IP до OUTPUT mangle, reroute через mark
# меняет out_iface на awg-X, MASQUERADE переписывает src, но reply ищет
# original socket по eth0_IP и не находит → TLS handshake'и таймаутят,
# `docker pull` висит, `apt-get` тормозит. Это known Linux limitation.
# Кому нужен local-curl через VPN — `curl --interface awg-X` явно.
# Раньше (до 0.2.28) использовали `("PREROUTING", "OUTPUT")` — отсюда жалобы
# на тормоза dockerd/apt'а после Apply'я catch-all direction'а.
_MARK_CHAINS = ("PREROUTING",)
# Cleanup'аем правила во всех legacy-цепях (orphan-clean при первом 0.2.28-Apply).
_READ_MARK_CHAINS = ("PREROUTING", "FORWARD", "OUTPUT")

# Comment-метка для self-bypass правил, чтобы их легко находить и не дублировать.
# Раньше было `waygate-ssh-bypass` (только порт 22) — переименовали в `self-bypass`
# когда добавили agent-port, чтобы имя отражало реальное покрытие.
_SELF_BYPASS_COMMENT = "waygate-self-bypass"
# Старый маркер — оставляем для миграции. apply'и со старым тегом будут заменены
# при следующем apply (idempotent reconcile с новым именем).
_LEGACY_SSH_BYPASS_COMMENT = "waygate-ssh-bypass"

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
    container_pid: int | None = None  # для container scope — нужен для cross-netns ipset sync


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
        container_pid=pid,
    )


async def _recover_mangle_if_incompatible(*, errors: list[str]) -> None:
    """Detect-and-recover: если mangle table стала "incompatible" для
    iptables-nft compat (например после native `nft add rule` в managed
    chain), сбрасываем её через `nft flush table ip mangle`.

    Без этого agent при reconcile падает на `iptables -t mangle ...` с
    `table 'mangle' is incompatible, use 'nft' tool`. Apply считается
    успешным с partial state — self-bypass'ы и mark не пересоздаются.

    Безопасно: mangle-table используется только waygate-агентом, его
    self-bypass'ы и mark-rules пересоздаются reconcile'ом сразу после
    flush. См. NFT-6 в `.claude/INCIDENT_2026_05_06_apply_flow.md`.
    """
    try:
        await run_command(["iptables", "-t", "mangle", "-S", "PREROUTING"])
    except CommandError as exc:
        if "incompatible" not in exc.stderr.lower():
            return
        logger.warning(
            "apply_rules: mangle table incompatible (likely native nft "
            "rules), сбрасываем через `nft flush table ip mangle` — "
            "apply пересоздаст self-bypass'ы и MARK-правила",
        )
        try:
            await run_command(["nft", "flush", "table", "ip", "mangle"])
        except CommandError as flush_exc:
            errors.append(f"[host] mangle recovery via nft flush: {flush_exc}")


async def _retry_netns_switch(
    op: Callable[[], Awaitable[None]],
    *,
    attempts: int = 2,
    backoff_s: float = 0.5,
) -> CommandError | None:
    """Повторяет netns-switch до `attempts` попыток с фиксированным backoff.

    Резерв поверх `awg_clients._wait_for_iface`: тот гарантирует появление iface
    после `docker run`, но если awg-quick не успел подняться к моменту первого
    `ip rule add ... iif awg-X`, повторим с задержкой. Возвращает последнюю
    ошибку или None при успехе.
    """
    last: CommandError | None = None
    for attempt in range(1, attempts + 1):
        try:
            await op()
        except CommandError as exc:
            last = exc
            if attempt < attempts:
                logger.warning(
                    "routing: netns-switch attempt {}/{} failed ({}), retry через {}s",
                    attempt,
                    attempts,
                    exc,
                    backoff_s,
                )
                await asyncio.sleep(backoff_s)
        else:
            return None
    return last


async def _ensure_awg_clients_in_netns_host(*, via_interfaces: set[str]) -> None:
    """Возвращает AWG-client'ы в host netns (если ранее были перевезены в
    container-netns под scope=container, а сейчас direction'ы со scope=host).

    Симметрично `_ensure_awg_clients_in_netns` — обе функции idempotent.
    """
    for iface in via_interfaces:
        client_name = await awg_clients.find_client_by_iface(iface=iface)
        if client_name is None:
            continue
        try:
            await awg_clients.redeploy_with_network_mode(name=client_name, network_mode="host")
        except awg_clients.AwgClientError as exc:
            raise CommandError(
                command=["awg-redeploy", client_name],
                returncode=1,
                stderr=str(exc),
            ) from exc


async def _ensure_awg_clients_in_netns(*, via_interfaces: set[str], scope_target: str) -> None:
    """Для каждого awg-iface из правил scope=container проверяет, что AWG-client-
    контейнер запущен с `--network container:<scope_target>`. Если нет —
    перезапускает.

    Без этого `nsenter -n iptables -A ... -j MARK ... -o awg-X` падает с
    `Cannot find device awg-X` — потому что iface awg-X живёт в host netns
    (если AWG-client запущен с `--network host`), а команда выполняется в
    netns scope_target'а.

    Цена переключения — кратковременный разрыв туннеля и потеря host'овой
    видимости (iface уезжает в netns scope_target'а). Это by design: один
    AWG-client = одна netns. Если у пользователя есть direction'ы со scope=host
    использующие тот же via_interface, они после этого сломаются — UI должен
    предупреждать оператора об этой исключительности.
    """
    expected_mode = f"container:{scope_target}"
    for iface in via_interfaces:
        client_name = await awg_clients.find_client_by_iface(iface=iface)
        if client_name is None:
            logger.warning(
                "routing: для iface {} не найден waygate-managed AWG-client "
                "(возможно iface создан вне waygate). Пропускаем netns-переключение.",
                iface,
            )
            continue
        try:
            await awg_clients.redeploy_with_network_mode(name=client_name, network_mode=expected_mode)
        except awg_clients.AwgClientError as exc:
            raise CommandError(
                command=["awg-redeploy", client_name],
                returncode=1,
                stderr=str(exc),
            ) from exc


def _parse_mark(value: str) -> int:
    return int(value, 16) if value.startswith("0x") else int(value)


async def _read_iptables_marks(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
) -> dict[str, dict[str, _ActiveMark]]:
    """Читает существующие mark-правила отдельно по каждой chain (PREROUTING + OUTPUT).

    Возвращает `{chain → {ipset_name → mark}}`. Раньше merge'или в один dict
    по `setdefault` — но если правило было только в OUTPUT, reconciler думал
    «уже есть» и не добавлял в PREROUTING. Forwarded-трафик (клиенты mac
    через AWG-server на хосте) не маркировался → шёл мимо туннеля.
    """
    marks: dict[str, dict[str, _ActiveMark]] = {chain: {} for chain in _READ_MARK_CHAINS}
    for chain in _READ_MARK_CHAINS:
        output = await run_command(
            [*ctx.command_prefix, family.iptables_cmd, "-t", "mangle", "-S", chain],
        )
        for line in output.splitlines():
            match = _IPTABLES_MARK_RE.search(line)
            if match is not None:
                ipset_name = match.group("ipset")
                marks[chain][ipset_name] = _ActiveMark(
                    ipset_name=ipset_name,
                    fwmark=_parse_mark(match.group("mark")),
                )
                continue
            # Catch-all (`-m mark --mark 0 -j MARK`) — храним под sentinel-ключом.
            default_match = _DEFAULT_EGRESS_MARK_RE.search(line)
            if default_match is not None:
                marks[chain][_DEFAULT_EGRESS_KEY] = _ActiveMark(
                    ipset_name=_DEFAULT_EGRESS_KEY,
                    fwmark=_parse_mark(default_match.group("mark")),
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
    chain: str,
) -> None:
    """Добавляет MARK-правило в указанную mangle-цепь.

    Раньше функция писала сразу в обе цепи (PREROUTING + OUTPUT), и reconciler
    решал «добавлять или нет» по объединённому состоянию обеих. Это приводило
    к скрытому состоянию когда одна цепь имела правило, другая — нет, и
    reconciler не дополнял. Теперь функция работает на одну цепь, а reconciler
    проверяет каждую отдельно (см. _read_iptables_marks).

    Sentinel `_DEFAULT_EGRESS_KEY` означает catch-all: `-m mark --mark 0`
    вместо `-m set --match-set`. Идёт `-A` (append) — после всех match-set
    правил, чтобы помеченные конкретными ipset'ами не перетирались.
    """
    if ipset_name == _DEFAULT_EGRESS_KEY:
        await run_command(
            [
                *ctx.command_prefix,
                family.iptables_cmd,
                "-t",
                "mangle",
                "-A",
                chain,
                "-m",
                "mark",
                "--mark",
                "0",
                "-j",
                "MARK",
                "--set-mark",
                str(fwmark),
            ],
        )
        return
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "mangle",
            "-A",
            chain,
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
    chain: str,
) -> None:
    """Удаляет MARK-правило из указанной mangle-цепи. `check=False` — если
    правила не было, не падаем (возможно только в одной цепи стояло).

    Sentinel `_DEFAULT_EGRESS_KEY` — снять unconditional MARK (`-m mark --mark 0`)
    вместо обычного match-set'а.
    """
    if ipset_name == _DEFAULT_EGRESS_KEY:
        await run_command(
            [
                *ctx.command_prefix,
                family.iptables_cmd,
                "-t",
                "mangle",
                "-D",
                chain,
                "-m",
                "mark",
                "--mark",
                "0",
                "-j",
                "MARK",
                "--set-mark",
                str(fwmark),
            ],
            check=False,
        )
        return
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "mangle",
            "-D",
            chain,
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
        check=False,
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


async def _iface_has_global_ipv6(*, ctx: _ScopeContext, iface: str) -> bool:
    """True если на iface есть GUA-IPv6 (любой `inet6` кроме link-local fe80::).

    AmneziaWG-туннели часто работают только в v4 (на стороне VPN-провайдера v6
    не настроен). В этом случае добавлять v6 mark/rule/route бессмысленно:
    `ip -6 route default dev awg-X` без link-peer уходит в чёрную дыру, и v6
    трафик с матчем по `<name>-v6` ipset молча теряется. По этому флагу мы
    скипаем v6-стек per-rule (см. #21a в BACKLOG).
    """
    output = await run_command(
        [*ctx.command_prefix, "ip", "-6", "addr", "show", "dev", iface],
        check=False,
    )
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("inet6 ") and not stripped.startswith("inet6 fe80"):
            return True
    return False


async def _read_masquerades(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
) -> set[str]:
    """Возвращает множество интерфейсов, для которых уже есть MASQUERADE в POSTROUTING.

    Без MASQUERADE на awg-iface исходящие пакеты уходят с public src eth0
    (потому что src выбирается ДО ip rule lookup). AWG-сервер дропает такие
    пакеты как spoofed.
    """
    output = await run_command(
        [*ctx.command_prefix, family.iptables_cmd, "-t", "nat", "-S", "POSTROUTING"],
        check=False,
    )
    # Формат `-A POSTROUTING -o <iface> -j MASQUERADE`.
    pattern = re.compile(r"-A\s+POSTROUTING\s+-o\s+(?P<iface>\S+)\s+-j\s+MASQUERADE\b")
    return {m.group("iface") for line in output.splitlines() if (m := pattern.search(line))}


async def _add_masquerade(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    interface: str,
) -> None:
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "nat",
            "-A",
            "POSTROUTING",
            "-o",
            interface,
            "-j",
            "MASQUERADE",
        ],
    )


async def _remove_masquerade(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    interface: str,
) -> None:
    await run_command(
        [
            *ctx.command_prefix,
            family.iptables_cmd,
            "-t",
            "nat",
            "-D",
            "POSTROUTING",
            "-o",
            interface,
            "-j",
            "MASQUERADE",
        ],
        check=False,
    )


async def _ensure_mss_clamp(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    interfaces: set[str],  # kept в API для обратной совместимости — clamp теперь без -o filter
) -> None:
    """Глобальный TCPMSS-clamp на mangle POSTROUTING без iface-фильтра.

    WireGuard добавляет ~80 байт overhead, MTU iface 1420 (vs eth0 1500).
    Без clamp'а большие пакеты (TLS Server Hello ~4KB) фрагментируются на
    IP-уровне. На сетях которые блокируют ICMP "fragmentation needed"
    (РФ-провайдеры) PMTU Discovery не работает → пакеты молча дропаются →
    connections виснут / медленный throughput.

    Раньше клампили только `-o awg-<X>` для каждого awg-iface'а из direction'ов.
    Это работало для исходящих SYN'ов на firstbyte (telephone→VPN→инет), но
    НЕ работало для **возвратных** SYN'ов (yandex.ru → firstbyte → AWG-server-iface
    → телефон) — там outbound iface это AWG-server (например awg0 в netns
    container'а), который НЕ был в `interfaces`. Backwards-MSS не клампился,
    yandex слал TCP-сегменты по 1410, и они не помещались в WG-iface'е к
    телефону.

    Решение — `-A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS
    --clamp-mss-to-pmtu` БЕЗ `-o`. Kernel сам берёт MTU выходного iface'а
    и клампит. Для eth0 (1500) MSS=1460, для awg-* (1420) MSS=1380. Никаких
    регрессий — просто более полное покрытие.

    Идемпотентно через `iptables -C` перед `-A`.
    """
    iptables = [*ctx.command_prefix, family.iptables_cmd, "-t", "mangle"]
    args = [
        "-p",
        "tcp",
        "--tcp-flags",
        "SYN,RST",
        "SYN",
        "-j",
        "TCPMSS",
        "--clamp-mss-to-pmtu",
    ]
    try:
        await run_command([*iptables, "-C", "POSTROUTING", *args])
    except CommandError:
        await run_command([*iptables, "-A", "POSTROUTING", *args])

    # Cleanup legacy-правил с `-o iface`-фильтром (от 0.2.19+) и из OUTPUT/FORWARD
    # (от 0.2.18). Они становятся избыточными при глобальном clamp'е.
    listing = await run_command([*iptables, "-S"], check=False)
    for line in listing.splitlines():
        if "TCPMSS" not in line or "clamp-mss-to-pmtu" not in line:
            continue
        if "-o " not in line and "POSTROUTING" in line:
            continue  # это наш желаемый, не трогаем
        if not line.startswith("-A "):
            continue
        delete_args = line.replace("-A ", "-D ", 1).split()
        await run_command([*iptables, *delete_args], check=False)


async def _replace_default_route(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    gateway: str,
    interface: str,
    table_id: int,
) -> None:
    """Atomic replace default-маршрута в таблице.

    Для AmneziaWG-туннелей (всегда POINTOPOINT) используем `ip route default
    dev <iface>` БЕЗ `via <gw>` для обоих family. Это P2P: ядро знает что у
    интерфейса один пир и сам инкапсулирует пакет в WireGuard, ARP не нужен.
    `via <gw> onlink` ломалось на конфигах где client.address = .1
    (`Nexthop has invalid gateway` — gateway не может равняться self-IP),
    и user должен был руками подбирать корректный gateway. Лишний шаг,
    устраняем.

    `gateway` остаётся в сигнатуре чтобы не ломать вызовы снизу (там
    via_gateway из RoutingRule), но игнорируется.
    """
    cmd = [
        *ctx.command_prefix,
        "ip",
        *family.ip_args,
        "route",
        "replace",
        "default",
        "dev",
        interface,
        "table",
        str(table_id),
    ]
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
    """Логическое имя `dns-youtube` → физическое `dns-youtube-v4`/`dns-youtube-v6`.

    Для catch-all rules (`is_default_egress=True`) ipset нет — возвращаем
    sentinel-ключ, по которому `_add_iptables_mark` решает писать unconditional
    `-m mark --mark 0` MARK вместо `-m set --match-set`.
    """
    if rule.is_default_egress:
        return _DEFAULT_EGRESS_KEY
    return f"{rule.ipset_name}{family.ipset_suffix}"


async def _ensure_mark(
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rule: RoutingRule,
    current_per_chain: dict[str, _ActiveMark],
) -> bool:
    """Гарантирует MARK-правило для rule в КАЖДОЙ из mangle-цепей (PREROUTING + OUTPUT).

    `current_per_chain` — словарь `{chain → mark}` где mark найден для нашего
    physical_name. Если в какой-то цепи нет mark или fwmark расходится —
    добавляем именно в эту цепь. Без раздельной проверки reconciler пропускал
    добавление в PREROUTING если правило уже было в OUTPUT (из старого apply'я),
    и forwarded трафик не маркировался.
    """
    physical_name = _ipset_name_for(rule=rule, family=family)
    changed = False
    for chain in _MARK_CHAINS:
        existing = current_per_chain.get(chain)
        if existing is not None and existing.fwmark == rule.fwmark:
            continue
        if existing is not None:
            await _remove_iptables_mark(
                ctx=ctx,
                family=family,
                ipset_name=existing.ipset_name,
                fwmark=existing.fwmark,
                chain=chain,
            )
        await _add_iptables_mark(ctx=ctx, family=family, ipset_name=physical_name, fwmark=rule.fwmark, chain=chain)
        changed = True
    return changed


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
    # Default-маршрут теперь всегда `dev iface` без gateway (P2P-туннель), для
    # обеих family. Считаем route актуальным если на нужном interface и без via.
    if current is not None and current.gateway is None and current.interface == rule.via_interface:
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
    current_marks: dict[str, dict[str, _ActiveMark]],
    current_ip_rules: dict[int, _ActiveIpRule],
    desired_physical_ipsets: set[str],
    desired_fwmarks: set[int],
    desired_tables: set[int],
    errors: list[str],
) -> None:
    """In-place cleanup: удаляет из current_* всё, чего нет в желаемом."""
    for chain, chain_marks in current_marks.items():
        for ipset_name, mark in list(chain_marks.items()):
            if ipset_name in desired_physical_ipsets:
                continue
            try:
                await _remove_iptables_mark(
                    ctx=ctx,
                    family=family,
                    ipset_name=ipset_name,
                    fwmark=mark.fwmark,
                    chain=chain,
                )
                chain_marks.pop(ipset_name, None)
            except CommandError as exc:
                errors.append(f"[{ctx.name}/{family.family}] remove orphan mark {ipset_name} в {chain}: {exc}")

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
) -> tuple[dict[str, dict[str, _ActiveMark]], dict[int, _ActiveIpRule]]:
    try:
        marks = await _read_iptables_marks(ctx=ctx, family=family)
    except CommandError as exc:
        errors.append(f"[{ctx.name}/{family.family}] read iptables marks: {exc}")
        marks = {chain: {} for chain in _READ_MARK_CHAINS}
    try:
        ip_rules = await _read_ip_rules(ctx=ctx, family=family)
    except CommandError as exc:
        errors.append(f"[{ctx.name}/{family.family}] read ip rules: {exc}")
        ip_rules = {}
    return marks, ip_rules


async def _sync_ipsets_to_container(*, ctx: _ScopeContext, ipset_names: set[str]) -> None:
    """Копирует ipset'ы из host netns в netns container'а (для scope=container).

    Ipsets начиная с kernel 4.19 — **per-netns**: тот что создан на хосте не
    виден внутри контейнера через nsenter. iptables `--match-set` падает с
    `Set X doesn't exist`. Решение — сделать `ipset save` на хосте и
    перезалить через `ipset restore -!` внутри netns'а target'а. Идемпотентно
    через `-!` (== `--exist`).
    """
    if ctx.container_pid is None:
        return  # scope=host — ничего не делаем
    for ipset_name in ipset_names:
        save_output = await run_command(["ipset", "save", ipset_name], check=False)
        if not save_output.strip():
            # На хосте ipset нет — оператор ещё не сделал sync для GeoIP/DNS/Custom-IPset.
            # Дальнейший _ensure_mark упадёт с понятной ошибкой "Set X doesn't exist".
            continue
        await run_command(
            ["nsenter", "-t", str(ctx.container_pid), "-n", "ipset", "restore", "-!"],
            stdin=save_output.encode(),
        )


async def _filter_v6_skippable_rules(
    *,
    ctx: _ScopeContext,
    rules: list[RoutingRule],
) -> list[RoutingRule]:
    """Возвращает только те rules, чей via_interface имеет GUA-IPv6.

    Кэширует per-iface результат `ip -6 addr show`, чтобы не дёргать его под
    каждое правило (один iface обычно используется несколькими). См. #21a.
    """
    v6_cache: dict[str, bool] = {}
    filtered: list[RoutingRule] = []
    for rule in rules:
        iface = rule.via_interface
        if iface not in v6_cache:
            v6_cache[iface] = await _iface_has_global_ipv6(ctx=ctx, iface=iface)
        if v6_cache[iface]:
            filtered.append(rule)
        else:
            logger.info(
                "routing: skip v6-стек для rule fwmark=0x{:x} (iface {} без GUA-IPv6)",
                rule.fwmark,
                iface,
            )
    return filtered


async def _apply_rules_in_scope_family(  # noqa: PLR0912 — orchestrator с reconcile/diff/MSS/MASQ — дробить ущерб читаемости
    *,
    ctx: _ScopeContext,
    family: _FamilyTools,
    rules: list[RoutingRule],
    errors: list[str],
) -> tuple[int, int]:
    """Применяет диф для одного scope+family. Возвращает (applied, skipped)."""
    # Per-rule skip v6-стека если via_interface не имеет GUA-IPv6 — иначе пакеты
    # с `--match-set <name>-v6` уходят в default route table → dev awg-X без v6
    # → silent drop.
    if family.family is _IpFamily.V6:
        rules = await _filter_v6_skippable_rules(ctx=ctx, rules=rules)

    desired_physical_ipsets = {_ipset_name_for(rule=rule, family=family) for rule in rules}
    desired_fwmarks = {rule.fwmark for rule in rules}
    desired_tables = {rule.table_id for rule in rules}
    desired_interfaces = {rule.via_interface for rule in rules}

    # Для scope=container — копируем нужные ipset'ы из host netns в netns target'а
    # (они per-netns в новых ядрах). Без этого iptables `--match-set` упадёт с
    # `Set X doesn't exist` внутри netns container'а.
    if ctx.container_pid is not None and desired_physical_ipsets:
        try:
            await _sync_ipsets_to_container(ctx=ctx, ipset_names=desired_physical_ipsets)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] sync ipsets to netns: {exc}")

    current_marks, current_ip_rules = await _read_state(ctx=ctx, family=family, errors=errors)
    try:
        current_masqs = await _read_masquerades(ctx=ctx, family=family)
    except CommandError as exc:
        errors.append(f"[{ctx.name}/{family.family}] read masquerades: {exc}")
        current_masqs = set()
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
    # Orphan-MASQUERADE: интерфейс больше не используется ни одним rule.
    for orphan_iface in current_masqs - desired_interfaces:
        try:
            await _remove_masquerade(ctx=ctx, family=family, interface=orphan_iface)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] remove orphan masquerade {orphan_iface}: {exc}")

    # MSS-clamp на awg-интерфейсах. Без него большие пакеты теряются на сетях
    # которые блокируют ICMP "fragmentation needed" — TCP retransmits, TLS
    # handshake timeout, "вроде работает но капец медленно".
    if desired_interfaces:
        try:
            await _ensure_mss_clamp(ctx=ctx, family=family, interfaces=desired_interfaces)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] mss-clamp: {exc}")

    applied = 0
    skipped = 0
    for rule in rules:
        physical_name = _ipset_name_for(rule=rule, family=family)
        try:
            current_per_chain = {
                chain: chain_marks[physical_name]
                for chain, chain_marks in current_marks.items()
                if physical_name in chain_marks
            }
            mark_changed = await _ensure_mark(
                ctx=ctx,
                family=family,
                rule=rule,
                current_per_chain=current_per_chain,
            )
            rule_changed = await _ensure_ip_rule(
                ctx=ctx,
                family=family,
                rule=rule,
                current=current_ip_rules.get(rule.fwmark),
            )
            route_changed = await _ensure_default_route(ctx=ctx, family=family, rule=rule)
            # MASQUERADE on out-iface: переписывает src на iface-IP при выходе в
            # туннель. Без этого пакеты, помеченные через `ip rule fwmark`,
            # уходят с public-src от eth0 → AWG-server'у выглядят spoofed → drop.
            masq_changed = False
            if rule.via_interface not in current_masqs:
                await _add_masquerade(ctx=ctx, family=family, interface=rule.via_interface)
                current_masqs.add(rule.via_interface)
                masq_changed = True
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] apply {physical_name}: {exc}")
            continue
        if mark_changed or rule_changed or route_changed or masq_changed:
            applied += 1
        else:
            skipped += 1
    return applied, skipped


async def _ensure_self_bypass(*, ctx: _ScopeContext, family: _FamilyTools) -> None:
    """Гарантирует что control-trafic'а агента НЕ маркируется match-set'ами.

    Self-lockout-guard: если ipset попадает свой собственный IP сервера (yandex VM
    в RU + GeoIP-RU direction), match-set --dst в OUTPUT помечает И SSH-ответы,
    И ответы агента control-plane'у → всё уходит в туннель → control теряется.
    Восстанавливать приходится через provider serial console.

    Защищаем два порта:
    1. SSH (`22`) — чтобы оператор всегда мог зайти руками.
    2. Agent-port (`settings.port`, обычно 7743) — чтобы control-plane мог дёргать
       агент даже при кривой direction. Без этого Apply через UI ронял агента,
       и второй UI-Apply ловил Server disconnected.

    В начале PREROUTING и OUTPUT mangle-цепей вешаем `RETURN` для каждого порта
    в обоих направлениях (sport/dport). RETURN прерывает обработку цепи раньше
    любого match-set, так что эти пакеты никогда не маркируются.

    Идемпотентно — проверяем по `-m comment --comment waygate-self-bypass`.
    Старые `waygate-ssh-bypass`-правила (от 0.2.13 hotfix'а) удаляем и заменяем
    на новые с покрытием agent-port.
    """
    iptables = [*ctx.command_prefix, family.iptables_cmd, "-t", "mangle"]
    listing = await run_command([*iptables, "-S"])

    # Удаляем legacy-правила (`waygate-ssh-bypass`) если они есть — заменяем
    # их на новый формат с покрытием agent-port. Без удаления старые остаются
    # в цепи но не мешают (сами пропускают tcp/22), просто захламляют.
    for line in listing.splitlines():
        if _LEGACY_SSH_BYPASS_COMMENT not in line:
            continue
        if not line.startswith("-A "):
            continue
        # `-A CHAIN <args>` → `-D CHAIN <args>` для удаления.
        delete_args = line.replace("-A ", "-D ", 1).split()
        await run_command([*iptables, *delete_args], check=False)

    # Свежий список после возможной чистки.
    listing = await run_command([*iptables, "-S"])
    have = {line for line in listing.splitlines() if _SELF_BYPASS_COMMENT in line}

    # Порт агента (обычно 7743). Защищаем дополнительно к SSH (22).
    agent_port = str(settings.port)
    # Port-specific bypass для INCOMING connections (NEW state на этих портах).
    desired_port_specs = [
        ("PREROUTING", "--dport", "22"),
        ("OUTPUT", "--sport", "22"),
        ("PREROUTING", "--dport", agent_port),
        ("OUTPUT", "--sport", agent_port),
    ]
    for chain, port_flag, port in desired_port_specs:
        marker = f"-A {chain}"
        # Проверяем что bypass-правило с этим port_flag/port уже стоит.
        if any(marker in line and f"{port_flag} {port}" in line for line in have):
            continue
        await run_command(
            [
                *iptables,
                "-I",
                chain,
                "1",
                "-p",
                "tcp",
                port_flag,
                port,
                "-m",
                "comment",
                "--comment",
                _SELF_BYPASS_COMMENT,
                "-j",
                "RETURN",
            ],
        )

    # ESTABLISHED/RELATED bypass — для возвратных пакетов любых уже-установленных
    # соединений. Без этого: mac коннектится к yandex VM по AWG-туннелю
    # (UDP/51820), conntrack treking входящего connection как NEW. Когда yandex
    # шлёт ответ obratно, dst=mac_IP попадает в RU-set (mac в РФ) → mark=1 →
    # ответ улетает в awg-firstbyte вместо eth0 → клиент теряет связь.
    # С ESTABLISHED-bypass'ом возвратные пакеты по существующим сессиям не
    # маркируются. NEW-исходящие (curl с yandex'а к RU-сайтам) — маркируются
    # как и положено, эффект direction'а сохраняется.
    for chain in ("PREROUTING", "OUTPUT"):
        marker = f"-A {chain}"
        if any(marker in line and "ESTABLISHED" in line and _SELF_BYPASS_COMMENT in line for line in have):
            continue
        await run_command(
            [
                *iptables,
                "-I",
                chain,
                "1",
                "-m",
                "conntrack",
                "--ctstate",
                "RELATED,ESTABLISHED",
                "-m",
                "comment",
                "--comment",
                _SELF_BYPASS_COMMENT,
                "-j",
                "RETURN",
            ],
        )

    # Local-destined bypass: incoming на собственный IP сервера (eth0/lo/docker
    # bridges) — никогда не маркируем. Без этого широкие direction'ы (например
    # catch-all `0.0.0.0/1 + 128.0.0.0/1` через NL) ломают сами себя:
    # incoming WG handshake на yandex_VM:42014/UDP имеет dst=local-IP, который
    # включён в `all-internet-v4` set → mark → table → awg-eurohoster →
    # handshake-reply улетает в NL вместо local-delivery в docker NAT →
    # клиент не подключается к AWG-server-контейнеру.
    # `addrtype --dst-type LOCAL` автоматически покрывает все local-адреса
    # (eth0, lo, docker0, любые bridges) без явного списка IP'шек.
    if not any("addrtype" in line and _SELF_BYPASS_COMMENT in line for line in have):
        await run_command(
            [
                *iptables,
                "-I",
                "PREROUTING",
                "1",
                "-m",
                "addrtype",
                "--dst-type",
                "LOCAL",
                "-m",
                "comment",
                "--comment",
                _SELF_BYPASS_COMMENT,
                "-j",
                "RETURN",
            ],
        )


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
        # Self-bypass СНАЧАЛА — даже если ниже падает, защита уже стоит.
        # Ошибки bypass'а не блокируют apply: если упало (например, нет xt_comment
        # модуля) — лучше сделать остальное с warning'ом, чем ронять весь apply.
        try:
            await _ensure_self_bypass(ctx=ctx, family=family)
        except CommandError as exc:
            errors.append(f"[{ctx.name}/{family.family}] self-bypass: {exc}")

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


_TOUCHED_NETNS_FILE = "/var/lib/waygate-agent/touched-netns.json"


def _read_touched_netns() -> set[str]:
    """Читает persistent-set имён container'ов в которых ранее apply'или
    scope=container правила. Используется для orphan-cleanup'а: если direction
    со scope_target=X удалён из desired, мы должны зайти в netns X и снести
    оставшиеся iptables/rule/route. Без persistent state agent не помнит между
    рестартами какие netns'ы трогал."""
    path = Path(_TOUCHED_NETNS_FILE)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return {str(item) for item in data}
    except (OSError, ValueError) as exc:
        logger.warning("touched-netns: не смог прочитать {}: {}", path, exc)
    return set()


def _write_touched_netns(targets: set[str]) -> None:
    path = Path(_TOUCHED_NETNS_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(targets)))
    except OSError as exc:
        logger.warning("touched-netns: не смог записать {}: {}", path, exc)


_LAST_APPLY_FILE = "/var/lib/waygate-agent/last-apply.json"


def _persist_apply_attempt(*, rule_count: int, applied: int, errors: list[str]) -> None:
    """Сохраняет результат последнего apply на диск: для self-recovery (server
    при OFFLINE→ONLINE может прочесть errors и решить retry'ить) и honest-status
    (`/v1/status` возвращает last_apply_errors)."""
    path = Path(_LAST_APPLY_FILE)
    payload = {
        "ts": time.time(),
        "rule_count": rule_count,
        "applied": applied,
        "errors": list(errors),
        "succeeded": not errors,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError as exc:
        logger.warning("last-apply: не смог записать {}: {}", path, exc)


def _load_last_apply() -> dict[str, object] | None:
    """Читает последний результат apply с диска или None если нет."""
    path = Path(_LAST_APPLY_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError) as exc:
        logger.warning("last-apply: не смог прочитать {}: {}", path, exc)
    return None


async def apply_rules(*, rules: list[RoutingRule]) -> ApplyRulesResponse:
    """Применяет правила маршрутизации идемпотентно.

    Группирует правила по (scope, scope_target). Для каждой группы строит
    `_ScopeContext` (host или nsenter в netns container'а) и вызывает один и тот
    же diff-applier с этим контекстом — отдельно для IPv4 и IPv6 стеков.

    Если desired пуст (все правила выключены / direction'ов больше нет) — всё
    равно делаем reconcile host-scope с пустым desired, чтобы удалить orphan'ы
    (висячие iptables/ip rule/ip route от прошлых apply). Без этого toggle
    direction → off → Apply не освобождал бы systemd-уровневые правила.

    Для scope=container ведём persistent-список «трогали ли мы этот netns»
    (`/var/lib/waygate-agent/touched-netns.json`). При apply без правил для
    netns'а, который раньше применяли — делаем cleanup-reconcile внутри netns'а
    (с пустым desired). Иначе orphan-state остаётся жить в чужой netns'е после
    удаления direction'а в UI.
    """
    desired = [rule for rule in rules if rule.enabled]
    errors: list[str] = []

    # NFT-6: если mangle-table стала incompatible для iptables-nft compat
    # (native nft rule в managed chain), сбрасываем перед reconcile —
    # иначе iptables -t mangle падает и apply записывает partial state.
    await _recover_mangle_if_incompatible(errors=errors)

    # Гарантируем что host-scope reconcile запускается даже при пустом desired.
    groups = _group_by_scope(desired)
    if (RoutingScope.HOST, None) not in groups:
        groups[(RoutingScope.HOST, None)] = []

    # Container-scope orphan-cleanup: если в `touched-netns.json` есть target,
    # которого нет в текущих desired-groups — добавляем его как пустой group.
    # При apply'е reconciler удалит всё внутри netns'а.
    desired_container_targets: set[str] = {
        target for (scope, target), _ in groups.items() if scope is RoutingScope.CONTAINER and target is not None
    }
    touched_targets = _read_touched_netns()
    for orphan_target in touched_targets - desired_container_targets:
        groups[(RoutingScope.CONTAINER, orphan_target)] = []

    # Записываем текущий снапшот desired-targets'ов чтобы следующий apply знал
    # что нужно cleanup'нуть если direction удалят.
    _write_touched_netns(desired_container_targets)

    total_applied = 0
    total_skipped = 0
    for (scope, target), group_rules in groups.items():
        # Для scope=container нужно сначала перезапустить relevant AWG-client'ы
        # с `--network container:<target>`, иначе iface awg-X живёт в host netns
        # и nsenter в чужой netns его не находит.
        if scope is RoutingScope.CONTAINER and target is not None:
            via_ifaces = {rule.via_interface for rule in group_rules}
            netns_err = await _retry_netns_switch(
                functools.partial(_ensure_awg_clients_in_netns, via_interfaces=via_ifaces, scope_target=target),
            )
            if netns_err is not None:
                errors.append(f"[container:{target}] netns-switch: {netns_err}")
                continue
        # Симметрично: для scope=host AWG-client должен быть в host netns. Если
        # пользователь раньше применял scope=container (iface уехал в чужой netns),
        # вернём awg-client с `--network host`.
        if scope is RoutingScope.HOST and group_rules:
            via_ifaces = {rule.via_interface for rule in group_rules}
            netns_err = await _retry_netns_switch(
                functools.partial(_ensure_awg_clients_in_netns_host, via_interfaces=via_ifaces),
            )
            if netns_err is not None:
                errors.append(f"[host] netns-switch back to host: {netns_err}")
                continue
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

    _persist_apply_attempt(rule_count=len(desired), applied=total_applied, errors=errors)

    return ApplyRulesResponse(applied=total_applied, skipped=total_skipped, errors=errors)
