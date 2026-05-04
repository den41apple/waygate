from pathlib import Path

from loguru import logger

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import ApplyDnsResponse, DnsRule

_CONFIG_PATH = Path("/etc/dnsmasq.d/waygate.conf")
_CONFIG_HEADER = "# Сгенерировано Waygate. Правится из контрольной панели, не вручную.\n\n"


def _normalize_domain(domain: str) -> str:
    """`*.example.com` → `example.com`. dnsmasq и так матчит все суффиксы."""
    if domain.startswith("*."):
        return domain[2:]
    return domain


def _render_config(*, rules: list[DnsRule]) -> str:
    """Формирует содержимое /etc/dnsmasq.d/waygate.conf.

    На каждый домен — ДВЕ отдельные `ipset=`-строки (одна для v4-set'а,
    одна для v6). Раздельный v4/v6 формат потому что dnsmasq 2.90 на
    `<setv4>,<setv6>` наблюдался как пустые ipset'ы (вероятно падает на
    add IPv4 → set family inet6 и обрывает запись в обе семьи).

    Один домен = одна строка (а не объединение N доменов через слеши в
    одну ipset-директиву). Длинные склеенные строки `ipset=/d1/d2/.../dN/set`
    dnsmasq не любит: на ~80+ доменах валится `error at line N` без точного
    указания причины. Build-line-per-domain — стабильно для любого размера.

    Имена ipset'ов — `<ipset_name>-v4` (hash:net family inet) и
    `<ipset_name>-v6` (hash:net family inet6); создаются через
    `_ensure_dual_family_ipsets`.
    """
    lines = [_CONFIG_HEADER]
    for rule in rules:
        unique_domains = sorted({_normalize_domain(domain=domain) for domain in rule.domains})
        if not unique_domains:
            continue
        for domain in unique_domains:
            lines.append(f"ipset=/{domain}/{rule.ipset_name}-v4\n")
            lines.append(f"ipset=/{domain}/{rule.ipset_name}-v6\n")
    return "".join(lines)


async def _ensure_dual_family_ipsets(*, rules: list[DnsRule], errors: list[str]) -> None:
    """Создаёт ipset-`{name}-v4` и `{name}-v6` для каждого DNS-правила.

    dnsmasq не создаёт ipset'ы автоматически — только пишет в существующие.
    Если ipset нет, iptables `--match-set <name>-v4 dst` падает с
    "Set ... doesn't exist". `-exist` делает create idempotent.
    """
    for rule in rules:
        for suffix, family in (("-v4", "inet"), ("-v6", "inet6")):
            try:
                await run_command(
                    [
                        "ipset",
                        "create",
                        "-exist",
                        f"{rule.ipset_name}{suffix}",
                        "hash:net",
                        "family",
                        family,
                        "hashsize",
                        "4096",
                        "maxelem",
                        "1000000",
                    ],
                )
            except CommandError as exc:
                # `already exists` (с другими параметрами в legacy-ipset'е) —
                # не критично, set всё равно existует и пригоден.
                if "already exists" not in str(exc):
                    errors.append(f"create ipset {rule.ipset_name}{suffix}: {exc}")


async def apply_dns(*, rules: list[DnsRule], config_path: Path | None = None) -> ApplyDnsResponse:
    """Записывает dnsmasq-конфиг и перезагружает сервис.

    Идемпотентно: если содержимое не изменилось, ничего не делаем и applied=0.
    Перед reload создаём ipset'ы (-v4/-v6) для каждого правила — без этого
    dnsmasq не сможет писать в них резолвенные IP.
    """
    target = config_path or _CONFIG_PATH
    new_content = _render_config(rules=rules)
    target.parent.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    # Создаём ipset'ы при каждом apply — `-exist` делает это idempotent.
    # Если конфиг не менялся, dnsmasq и так знает про них; мы лишь гарантируем
    # что `iptables --match-set` потом найдёт их.
    await _ensure_dual_family_ipsets(rules=rules, errors=errors)

    current = target.read_text() if target.exists() else ""
    if current == new_content:
        logger.debug("dns: конфиг не изменился — пропускаю reload")
        return ApplyDnsResponse(applied=0, errors=errors)

    target.write_text(new_content)

    # `systemctl reload` для dnsmasq = SIGHUP, который перечитывает ТОЛЬКО
    # /etc/hosts и DHCP-leases. Конфиг-файлы (`ipset=`, `server=`) подхватываются
    # ИСКЛЮЧИТЕЛЬНО при полном restart'е. Без restart'а наши ipset-директивы
    # остаются невидимы для dnsmasq → ipset не наполняется → iptables match-set
    # ничего не находит → весь DNS-routing flow молча не работает.
    try:
        await run_command(["systemctl", "restart", "dnsmasq"])
    except CommandError as exc:
        errors.append(f"restart dnsmasq: {exc}")
        logger.warning("dns: dnsmasq restart не удался: {}", exc)
    return ApplyDnsResponse(applied=len(rules), errors=errors)
