"""Парсер/сериализатор конфига AmneziaWG 2.0 (.conf-файл).

Контракт переиспользуется агентом (валидация перед `awg-quick up`) и control-plane'ом
(extracting peer-endpoint/peer-pubkey для UI). Все 19 параметров `[Interface]` и 5
параметров `[Peer]` как в дизайн-формате пользователя.
"""

import base64
import re

from pydantic import BaseModel, Field, field_validator

# Base64-ключи AmneziaWG/WireGuard — ровно 32 байта закодированных как 44 символа.
_BASE64_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
# Endpoint: host:port. host может быть IPv4/IPv6/DNS-имя; port — 1..65535.
_ENDPOINT_RE = re.compile(r"^.+:\d{1,5}$")


def _validate_b64_key(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not _BASE64_KEY_RE.match(value):
        raise ValueError(f"{field_name}: не похож на base64 wireguard-ключ (32 байта = 44 символа)")
    # Дополнительно убеждаемся что декодируется ровно в 32 байта.
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{field_name}: невалидный base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{field_name}: ключ должен декодироваться в 32 байта, не {len(decoded)}")
    return value


class AwgInterfaceConfig(BaseModel):
    """`[Interface]` секция .conf-файла AmneziaWG 2.0.

    Стандартные wireguard-поля + amnezia-specific (Jc/Jmin/Jmax — junk packets,
    S1-S4 — init/response/cookie/transport junk sizes, H1-H4 — header types,
    I1-I5 — additional obfuscation parameters в 2.0).
    """

    address: str = Field(description="CIDR клиента в туннеле, e.g. 10.66.66.2/24")
    private_key: str = Field(description="Base64 32-байтный приватный ключ клиента")
    dns: str | None = Field(default=None, description="DNS-сервера через запятую")
    listen_port: int | None = Field(default=None, description="UDP-порт (опционально)")
    # MTU=1280 — безопасный default для двойной AWG-обёртки
    # (phone → AWG-server → AWG-client → upstream-VPN). При дефолтном awg-quick
    # MTU=1420 для двойного туннеля eth0(1500) − 80 (outer AWG) − 80 (inner AWG)
    # = 1340 effective; MSS-clamp на 1380 фрагментирует TCP. На 1280 запас
    # хватает на любые конфигурации до 4-х слойных туннелей. Закрывает NFT-5
    # из 2026-05-06 (см. INCIDENT_2026_05_06_apply_flow.md).
    mtu: int | None = Field(default=1280, description="MTU интерфейса")

    # AmneziaWG-specific (2.0+).
    # Jc/Jmin/Jmax/S1-S4 — числа (junk packet count и размеры).
    # H1-H4 — диапазоны вида "min-max" (рандом из диапазона при handshake'е),
    # либо одно число — храним как строку, awg-quick передаёт как есть.
    # I1-I5 — opaque-blob'ы: либо `<b 0xDEADBEEF>` (бинарь в hex), либо
    # plain-string. Длина может быть огромной — никаких числовых преобразований.
    jc: int | None = Field(default=None, description="Junk packet count")
    jmin: int | None = Field(default=None, description="Junk packet min size")
    jmax: int | None = Field(default=None, description="Junk packet max size")
    s1: int | None = Field(default=None, description="Init packet junk size")
    s2: int | None = Field(default=None, description="Response packet junk size")
    s3: int | None = Field(default=None, description="Cookie packet junk size (2.0+)")
    s4: int | None = Field(default=None, description="Transport packet junk size (2.0+)")
    h1: str | None = Field(default=None, description="Init packet header magic (число или диапазон min-max)")
    h2: str | None = Field(default=None, description="Response packet header magic")
    h3: str | None = Field(default=None, description="Cookie packet header magic")
    h4: str | None = Field(default=None, description="Transport packet header magic")
    i1: str | None = Field(default=None, description="AmneziaWG 2.0 obfuscation pattern I1 (opaque)")
    i2: str | None = Field(default=None, description="AmneziaWG 2.0 obfuscation pattern I2")
    i3: str | None = Field(default=None, description="AmneziaWG 2.0 obfuscation pattern I3")
    i4: str | None = Field(default=None, description="AmneziaWG 2.0 obfuscation pattern I4")
    i5: str | None = Field(default=None, description="AmneziaWG 2.0 obfuscation pattern I5")

    @field_validator("private_key")
    @classmethod
    def _check_private_key(cls, value: str) -> str:
        result = _validate_b64_key(value, field_name="PrivateKey")
        if result is None:
            raise ValueError("PrivateKey: обязательное поле")
        return result


class AwgPeerConfig(BaseModel):
    """`[Peer]` секция — описание единственного пира (VPN-сервера) для клиента."""

    public_key: str = Field(description="Base64 32-байтный публичный ключ сервера")
    allowed_ips: str = Field(description="CIDR'ы что роутить через туннель, e.g. 0.0.0.0/0")
    endpoint: str = Field(description="host:port VPN-сервера")
    preshared_key: str | None = Field(default=None, description="Опциональный pre-shared ключ")
    persistent_keepalive: int | None = Field(default=None, description="Сек, обычно 25")

    @field_validator("public_key")
    @classmethod
    def _check_public_key(cls, value: str) -> str:
        result = _validate_b64_key(value, field_name="PublicKey")
        if result is None:
            raise ValueError("PublicKey: обязательное поле")
        return result

    @field_validator("preshared_key")
    @classmethod
    def _check_preshared(cls, value: str | None) -> str | None:
        return _validate_b64_key(value, field_name="PresharedKey")

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        if not _ENDPOINT_RE.match(value):
            raise ValueError("Endpoint должен быть в формате host:port")
        return value


class AwgFullConfig(BaseModel):
    """Полный .conf-файл клиента: одна `[Interface]` + одна `[Peer]` секция."""

    interface: AwgInterfaceConfig
    peer: AwgPeerConfig


# Маппинг snake_case → INI-keys (ключи .conf — PascalCase / SCREAMING).
_INTERFACE_KEY_MAP = {
    "Address": "address",
    "DNS": "dns",
    "PrivateKey": "private_key",
    "ListenPort": "listen_port",
    "MTU": "mtu",
    "Jc": "jc",
    "Jmin": "jmin",
    "Jmax": "jmax",
    "S1": "s1",
    "S2": "s2",
    "S3": "s3",
    "S4": "s4",
    "H1": "h1",
    "H2": "h2",
    "H3": "h3",
    "H4": "h4",
    "I1": "i1",
    "I2": "i2",
    "I3": "i3",
    "I4": "i4",
    "I5": "i5",
}

_PEER_KEY_MAP = {
    "PublicKey": "public_key",
    "PresharedKey": "preshared_key",
    "AllowedIPs": "allowed_ips",
    "Endpoint": "endpoint",
    "PersistentKeepalive": "persistent_keepalive",
}

# H1-H4 — НЕ int (могут быть диапазоны "min-max"). I1-I5 — НЕ int (opaque blob).
_INTEGER_FIELDS_INTERFACE = {"listen_port", "mtu", "jc", "jmin", "jmax", "s1", "s2", "s3", "s4"}
_INTEGER_FIELDS_PEER = {"persistent_keepalive"}


def _parse_section(*, lines: list[str]) -> dict[str, str]:
    """Парсит body одной INI-секции в dict, выкидывает комменты и пустые строки."""
    fields: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise ValueError(f"Неожиданная строка в секции: {raw_line!r}")
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields


def _split_sections(text: str) -> dict[str, list[str]]:
    """Разделяет .conf на секции по `[SectionName]` заголовкам."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            if section_name in sections:
                raise ValueError(f"Дублирующаяся секция: [{section_name}] (поддерживается только одна)")
            current = sections.setdefault(section_name, [])
        elif current is not None:
            current.append(raw_line)
    return sections


def _coerce(*, raw: dict[str, str], key_map: dict[str, str], int_fields: set[str]) -> dict[str, object]:
    """Переименовывает INI-ключи в snake_case + парсит integer-поля."""
    out: dict[str, object] = {}
    for ini_key, value in raw.items():
        snake = key_map.get(ini_key)
        if snake is None:
            # Неизвестный ключ — игнорируем (форвард-совместимость с будущими полями).
            continue
        if snake in int_fields:
            try:
                out[snake] = int(value)
            except ValueError as exc:
                raise ValueError(f"{ini_key}: ожидалось целое число, получено {value!r}") from exc
        else:
            out[snake] = value
    return out


def parse_awg_config(text: str) -> AwgFullConfig:
    """Парсит .conf-текст в Pydantic-модель. Кидает ValueError на любую проблему."""
    sections = _split_sections(text)
    if "Interface" not in sections:
        raise ValueError("в .conf нет секции [Interface]")
    if "Peer" not in sections:
        raise ValueError("в .conf нет секции [Peer]")

    interface_raw = _parse_section(lines=sections["Interface"])
    peer_raw = _parse_section(lines=sections["Peer"])

    interface_kwargs = _coerce(
        raw=interface_raw,
        key_map=_INTERFACE_KEY_MAP,
        int_fields=_INTEGER_FIELDS_INTERFACE,
    )
    peer_kwargs = _coerce(
        raw=peer_raw,
        key_map=_PEER_KEY_MAP,
        int_fields=_INTEGER_FIELDS_PEER,
    )

    return AwgFullConfig(
        interface=AwgInterfaceConfig(**interface_kwargs),
        peer=AwgPeerConfig(**peer_kwargs),
    )


def serialize_awg_config(config: AwgFullConfig) -> str:
    """Сериализует обратно в .conf-формат (для записи на target).

    Только заданные (non-None) поля попадают в вывод — нечего захламлять файл
    дефолтами. Порядок ключей фиксирован и совпадает с дизайн-форматом.
    """
    interface_order = list(_INTERFACE_KEY_MAP.keys())
    peer_order = list(_PEER_KEY_MAP.keys())

    def _format_section(*, name: str, kv: list[tuple[str, str]]) -> list[str]:
        out = [f"[{name}]"]
        out.extend(f"{key} = {value}" for key, value in kv)
        return out

    interface_kv: list[tuple[str, str]] = []
    interface_dump = config.interface.model_dump(exclude_none=True)
    for ini_key in interface_order:
        snake = _INTERFACE_KEY_MAP[ini_key]
        if snake in interface_dump:
            interface_kv.append((ini_key, str(interface_dump[snake])))

    # `Table = off` — критичная директива wg-quick: подними netdev/IP/peer/sysctl,
    # но НЕ трогай ip rule / ip route. Без этого awg-quick по дефолту с
    # `AllowedIPs = 0.0.0.0/0` делает hijack всего трафика хоста через VPN
    # (`ip rule not fwmark 51820 table 51820 → ip route 0.0.0.0/0 dev awg-X`),
    # и если туннель не поднялся, хост теряет связь — теряется SSH, выход
    # только через console-доступ хостера. Waygate использует AWG-клиент как
    # просто netdev на хосте; маршрутизацию делает agent через apply_rules с
    # уникальным fwmark/table_id.
    interface_kv.append(("Table", "off"))

    peer_kv: list[tuple[str, str]] = []
    peer_dump = config.peer.model_dump(exclude_none=True)
    for ini_key in peer_order:
        snake = _PEER_KEY_MAP[ini_key]
        if snake in peer_dump:
            peer_kv.append((ini_key, str(peer_dump[snake])))

    lines = _format_section(name="Interface", kv=interface_kv)
    lines.append("")
    lines.extend(_format_section(name="Peer", kv=peer_kv))
    return "\n".join(lines) + "\n"
