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

    Для каждого правила одна строка `ipset=/<domain>/.../<setv4>,<setv6>` —
    dnsmasq пишет A-записи в `<setv4>` (hash:net family inet), AAAA — в
    `<setv6>` (hash:net family inet6). Соответствие именам — `<ipset_name>-v4`
    и `<ipset_name>-v6`.
    """
    lines = [_CONFIG_HEADER]
    for rule in rules:
        unique_domains = sorted({_normalize_domain(domain=domain) for domain in rule.domains})
        if not unique_domains:
            continue
        domains_part = "/".join(unique_domains)
        lines.append(f"ipset=/{domains_part}/{rule.ipset_name}-v4,{rule.ipset_name}-v6\n")
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

    try:
        await run_command(["systemctl", "reload", "dnsmasq"])
    except CommandError as exc:
        errors.append(f"reload dnsmasq: {exc}")
        logger.warning("dns: dnsmasq reload не удался: {}", exc)
    return ApplyDnsResponse(applied=len(rules), errors=errors)
