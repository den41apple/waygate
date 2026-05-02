from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

# Регексы под форматы Linux netlink-объектов. Имена идут в субпроцессы как-есть,
# поэтому строго ограничиваем безопасным алфавитом.
IpsetName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,31}$")]
InterfaceName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,15}$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2}$")]
DomainPattern = Annotated[str, StringConstraints(pattern=r"^[*A-Za-z0-9._-]{1,253}$")]
DnsRuleName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")]

# ############################################
# #  Перечисления
# ############################################


class TlsMode(StrEnum):
    UPLOAD = "upload"  # загрузить cert.pem и key.pem через API
    PATH = "path"  # указать путь к файлам на сервере
    ACME = "acme"  # получить через Let's Encrypt


class AcmeChallenge(StrEnum):
    HTTP01 = "http01"  # HTTP-01: временный endpoint на :80
    DNS01 = "dns01"  # DNS-01: через API DNS-провайдера


class DnsProvider(StrEnum):
    CLOUDFLARE = "cloudflare"  # Cloudflare DNS API
    DESEC = "desec"  # deSEC (бесплатный, рекомендован)
    ROUTE53 = "route53"  # AWS Route 53


class TunnelStatus(StrEnum):
    UP = "up"  # туннель работает нормально
    DOWN = "down"  # туннель недоступен
    DEGRADED = "degraded"  # туннель частично работает (не все пиры)


class UpdateStatus(StrEnum):
    RESTARTING = "restarting"  # агент принял обновление и перезапускается


# ############################################
# #  /v1/status
# ############################################


class AwgContainerInfo(BaseModel):
    name: str = Field(description="Имя Docker-контейнера AmneziaWG")
    interface: str = Field(description="Сетевой интерфейс (awg0, awg1 ...)")


class AgentStatus(BaseModel):
    version: str = Field(description="Версия агента")
    uptime_seconds: int = Field(description="Время работы в секундах")
    hostname: str = Field(description="Имя хоста сервера")
    awg_containers: list[AwgContainerInfo] = Field(description="Обнаруженные AWG-контейнеры")
    rules_applied: int = Field(description="Количество активных правил маршрутизации")
    tls_mode: TlsMode | None = Field(default=None, description="Текущий режим TLS")


# ############################################
# #  /v1/metrics
# ############################################


class TunnelMetrics(BaseModel):
    peer: str = Field(description="Публичный ключ пира")
    endpoint: str | None = Field(description="Адрес пира (ip:port)")
    rx_bytes: int = Field(description="Принято байт")
    tx_bytes: int = Field(description="Передано байт")


class MetricsSnapshot(BaseModel):
    timestamp: datetime = Field(description="Время снимка метрик")
    tunnels: list[TunnelMetrics] = Field(description="Метрики по каждому пиру")


# ############################################
# #  /v1/tunnels
# ############################################


class PeerInfo(BaseModel):
    public_key: str = Field(description="Публичный ключ пира WireGuard")
    endpoint: str | None = Field(description="Адрес пира (ip:port)")
    last_handshake: datetime | None = Field(description="Время последнего handshake")
    rx_bytes: int = Field(description="Принято байт")
    tx_bytes: int = Field(description="Передано байт")


class TunnelInfo(BaseModel):
    container_name: str = Field(description="Имя Docker-контейнера")
    interface: str = Field(description="Сетевой интерфейс")
    peers: list[PeerInfo] = Field(description="Список пиров")
    status: TunnelStatus = Field(description="Состояние туннеля")


class TunnelsResponse(BaseModel):
    tunnels: list[TunnelInfo] = Field(description="Все AWG-туннели на сервере")


# ############################################
# #  /v1/rules/apply
# ############################################


class RoutingRule(BaseModel):
    country: CountryCode = Field(description="Код страны ISO 3166-1 alpha-2 (RU, BY ...)")
    ipset_name: IpsetName = Field(description="Имя ipset-множества (russia, belarus ...)")
    fwmark: int = Field(ge=1, le=0xFFFFFFFF, description="Метка пакетов для policy routing")
    table_id: int = Field(ge=1, le=252, description="Номер таблицы маршрутизации")
    via_interface: InterfaceName = Field(description="Исходящий интерфейс (awg0 ...)")
    via_gateway: str = Field(description="IP-адрес шлюза")
    enabled: bool = Field(description="Активно ли правило")


class ApplyRulesRequest(BaseModel):
    rules: list[RoutingRule] = Field(description="Желаемое состояние правил — агент применяет diff")


class ApplyRulesResponse(BaseModel):
    applied: int = Field(description="Добавлено или изменено правил")
    skipped: int = Field(description="Правил без изменений")
    errors: list[str] = Field(default_factory=list, description="Ошибки применения")


# ############################################
# #  /v1/geoip/sync
# ############################################


class GeoIpSyncRequest(BaseModel):
    country: CountryCode = Field(description="Код страны (RU, BY ...)")
    ipset_name: IpsetName = Field(description="Имя ipset-множества")
    source_url: str = Field(description="URL zone-файла (ipdeny или RIPE)")
    custom_cidrs: list[str] = Field(default_factory=list, description="Дополнительные CIDR-блоки")


class GeoIpSyncResponse(BaseModel):
    cidrs_loaded: int = Field(description="Загружено CIDR-блоков")
    ipset_name: str = Field(description="Имя применённого ipset")
    duration_ms: int = Field(description="Время выполнения в миллисекундах")


# ############################################
# #  /v1/dns/apply
# ############################################


class DnsRule(BaseModel):
    name: DnsRuleName = Field(description="Название группы доменов")
    domains: list[DomainPattern] = Field(description="Домены — dnsmasq пишет резолвы в ipset")
    ipset_name: IpsetName = Field(description="Имя ipset для резолвов")


class ApplyDnsRequest(BaseModel):
    rules: list[DnsRule] = Field(description="Желаемое состояние DNS-правил — агент применяет diff")


class ApplyDnsResponse(BaseModel):
    applied: int = Field(description="Применено правил")
    errors: list[str] = Field(default_factory=list, description="Ошибки применения")


# ############################################
# #  /v1/tls/apply
# ############################################


class TlsConfig(BaseModel):
    mode: TlsMode = Field(description="Режим получения сертификата")
    port: int = Field(default=7743, description="Порт агента")

    # mode = upload
    cert_pem: str | None = Field(default=None, description="Содержимое cert.pem (base64)")
    key_pem: str | None = Field(default=None, description="Содержимое key.pem (base64)")

    # mode = path
    cert_path: str | None = Field(default=None, description="Путь к cert.pem на сервере")
    key_path: str | None = Field(default=None, description="Путь к key.pem на сервере")

    # mode = acme
    domains: list[str] = Field(default_factory=list, description="Домены для сертификата")
    email: str | None = Field(default=None, description="Email для Let's Encrypt")
    challenge: AcmeChallenge | None = Field(default=None, description="Тип ACME-challenge")
    dns_provider: DnsProvider | None = Field(default=None, description="DNS-провайдер для DNS-01")
    dns_api_key: str | None = Field(default=None, description="API-ключ DNS-провайдера")

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "TlsConfig":
        """Проверяет что заполнены нужные поля для выбранного режима."""
        if self.mode is TlsMode.UPLOAD:
            if not self.cert_pem or not self.key_pem:
                raise ValueError("mode=upload требует cert_pem и key_pem")
        elif self.mode is TlsMode.PATH:
            if not self.cert_path or not self.key_path:
                raise ValueError("mode=path требует cert_path и key_path")
        elif self.mode is TlsMode.ACME:
            if not self.domains or not self.email or not self.challenge:
                raise ValueError("mode=acme требует domains, email и challenge")
            if self.challenge is AcmeChallenge.DNS01 and (not self.dns_provider or not self.dns_api_key):
                raise ValueError("challenge=dns01 требует dns_provider и dns_api_key")
        return self


class TlsApplyResponse(BaseModel):
    cert_path: str = Field(description="Путь к применённому сертификату на сервере")
    expires_at: datetime = Field(description="Дата истечения сертификата")
    domains: list[str] = Field(description="Домены в сертификате")


# ############################################
# #  /v1/update
# ############################################


class UpdateRequest(BaseModel):
    version: str = Field(description="Целевая версия агента (например 0.2.0)")
    wheel_url: str = Field(description="URL wheel-файла на GitHub Releases")


class UpdateResponse(BaseModel):
    previous_version: str = Field(description="Версия агента до обновления")
    status: UpdateStatus = Field(description="Статус — всегда restarting")


# ############################################
# #  /v1/token/rotate
# ############################################


class TokenRotateResponse(BaseModel):
    token: str = Field(description="Новый Bearer-токен для аутентификации")
