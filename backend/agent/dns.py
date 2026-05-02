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

    Для каждого правила одна строка `ipset=/<domain>/.../<ipset_name>` — dnsmasq
    парсит её как «любой запрос к этим доменам и поддоменам пишется в указанный
    ipset».
    """
    lines = [_CONFIG_HEADER]
    for rule in rules:
        unique_domains = sorted({_normalize_domain(domain=domain) for domain in rule.domains})
        if not unique_domains:
            continue
        domains_part = "/".join(unique_domains)
        lines.append(f"ipset=/{domains_part}/{rule.ipset_name}\n")
    return "".join(lines)


async def apply_dns(*, rules: list[DnsRule], config_path: Path | None = None) -> ApplyDnsResponse:
    """Записывает dnsmasq-конфиг и перезагружает сервис.

    Идемпотентно: если содержимое не изменилось, ничего не делаем и applied=0.
    """
    target = config_path or _CONFIG_PATH
    new_content = _render_config(rules=rules)
    target.parent.mkdir(parents=True, exist_ok=True)

    current = target.read_text() if target.exists() else ""
    if current == new_content:
        logger.debug("dns: конфиг не изменился — пропускаю reload")
        return ApplyDnsResponse(applied=0, errors=[])

    target.write_text(new_content)

    errors: list[str] = []
    try:
        await run_command(["systemctl", "reload", "dnsmasq"])
    except CommandError as exc:
        errors.append(f"reload dnsmasq: {exc}")
        logger.warning("dns: dnsmasq reload не удался: {}", exc)
    return ApplyDnsResponse(applied=len(rules), errors=errors)
