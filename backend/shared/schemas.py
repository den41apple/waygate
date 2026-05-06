from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

# Регексы под форматы Linux netlink-объектов. Имена идут в субпроцессы как-есть,
# поэтому строго ограничиваем безопасным алфавитом.
IpsetName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,31}$")]
# Note: Linux IFNAMSIZ=16 (≤15 символов) для реальных netdev'ов вроде `awg0`.
# Но в UI пользователь может вводить как netdev, так и docker-container имя
# (например `waygate-amnezia-client-nl` для container-scope правил), поэтому
# держим длину до 64. Если на агенте такое имя не существует как netdev —
# `ip route` выдаст осмысленную ошибку, она прилетит в ApplyRulesResponse.errors.
InterfaceName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,64}$")]
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


class AwgContainerRole(StrEnum):
    CLIENT = "client"  # развёрнут через Waygate (есть label io.waygate.role=client)
    EXTERNAL = "external"  # подняли руками или это server-side AWG-контейнер


class AwgContainerInfo(BaseModel):
    name: str = Field(description="Имя Docker-контейнера AmneziaWG")
    interface: str = Field(description="Сетевой интерфейс (awg0, awg1 ...)")
    role: AwgContainerRole = Field(
        default=AwgContainerRole.EXTERNAL,
        description="Откуда взялся контейнер: client (наш) или external (user'ский)",
    )


class AgentStatus(BaseModel):
    version: str = Field(description="Версия агента")
    uptime_seconds: int = Field(description="Время работы в секундах")
    hostname: str = Field(description="Имя хоста сервера")
    awg_containers: list[AwgContainerInfo] = Field(description="Обнаруженные AWG-контейнеры")
    rules_applied: int = Field(description="Количество активных правил маршрутизации")
    tls_mode: TlsMode | None = Field(default=None, description="Текущий режим TLS")
    # Back-compat: новый сервер может прочитать честный счётчик applied vs requested,
    # старый агент не отдаёт эти поля (default'ы) — server'у безопасно их getattr'ить.
    last_apply_errors: list[str] = Field(
        default_factory=list,
        description="Ошибки последнего apply (пусто → success)",
    )
    last_apply_succeeded: bool = Field(
        default=True,
        description="True если последний apply прошёл без errors[]",
    )


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


class RoutingScope(StrEnum):
    HOST = "host"  # iptables/ip rule на самом хосте
    CONTAINER = "container"  # внутри netns docker-контейнера через nsenter


class RoutingRule(BaseModel):
    country: CountryCode | None = Field(
        default=None,
        description="Код страны ISO 3166-1 alpha-2 (RU, BY ...) либо None для DNS/IPset-правил",
    )
    ipset_name: IpsetName | None = Field(
        default=None,
        description="Имя ipset (russia, belarus ...). None для catch-all default-egress правил",
    )
    fwmark: int = Field(ge=1, le=0xFFFFFFFF, description="Метка пакетов для policy routing")
    table_id: int = Field(ge=1, le=252, description="Номер таблицы маршрутизации")
    via_interface: InterfaceName = Field(description="Исходящий интерфейс (awg0 ...)")
    via_gateway: str = Field(description="IP-адрес шлюза")
    enabled: bool = Field(description="Активно ли правило")
    scope: RoutingScope = Field(default=RoutingScope.HOST, description="Где применять правило")
    scope_target: str | None = Field(
        default=None,
        description="Имя docker-контейнера для scope=container (e.g. amnezia-awg2)",
    )
    is_default_egress: bool = Field(
        default=False,
        description=(
            "Catch-all: маркировать ВСЕ пакеты с mark=0 (не помеченные другими "
            "правилами) этим fwmark'ом. UNIQUE per (server, scope) — гарантируется "
            "control-plane'ом. Несовместимо с `ipset_name`."
        ),
    )


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


# ############################################
# #  /v1/clients (AmneziaWG-клиенты — managed deployment)
# ############################################


# Имя контейнера: для управления docker'ом и для file-system path'а под конфиг.
# Финальное имя контейнера на target — `waygate-amnezia-client-<name>`.
AwgClientName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,29}$")]


class AwgClientStatus(StrEnum):
    PENDING = "pending"  # docker run ещё не отработал
    RUNNING = "running"  # контейнер up, awg-quick поднял интерфейс
    STOPPED = "stopped"  # контейнер существует но остановлен
    ERROR = "error"  # docker run упал, или контейнер crashed


class CreateAwgClientRequest(BaseModel):
    name: AwgClientName = Field(description="Короткий уникальный идентификатор (a-z0-9-)")
    config_text: str = Field(description="Полный .conf-файл клиента в plaintext")


class AwgClientInfo(BaseModel):
    """Информация о клиенте для возврата control-plane'у и UI."""

    name: str = Field(description="Идентификатор клиента")
    container_name: str = Field(description="Полное docker-имя контейнера")
    # Optional для совместимости со старыми агентами (до #20). Server-side
    # fallback в `clients.py::_to_response` через `iface_name_for(name=...)`.
    # Новые агенты всегда возвращают это поле.
    interface_name: str | None = Field(
        default=None,
        description=(
            "Имя netdev'а внутри host/container netns (например `awg-myclient`). "
            "Генерируется агентом из `name` с учётом IFNAMSIZ=16 — фронт берёт "
            "это поле вместо локальной формулы. None — старый агент без #20, "
            "control-plane подсунет fallback из shared/awg_naming.py."
        ),
    )
    status: AwgClientStatus = Field(description="Текущий статус")
    peer_endpoint: str | None = Field(default=None, description="host:port VPN-сервера из [Peer]")
    peer_pubkey: str | None = Field(default=None, description="Публичный ключ VPN-сервера из [Peer]")
    interface_address: str | None = Field(default=None, description="Address из [Interface]")


class CreateAwgClientResponse(BaseModel):
    client: AwgClientInfo


class ListAwgClientsResponse(BaseModel):
    clients: list[AwgClientInfo]


class AwgClientActionResponse(BaseModel):
    """Ответ на /start, /stop, /delete — просто текущий статус."""

    name: str
    status: AwgClientStatus


# ############################################
# #  /v1/containers (любые docker-контейнеры на target)


class ContainerInfo(BaseModel):
    """Запущенный или существующий docker-контейнер на target.

    Используется в UI для выпадающего списка `scope_target` при scope=container —
    оператору нужно выбирать из реальных контейнеров, а не вводить имя руками.
    """

    name: str = Field(description="Имя контейнера (docker `--name`)")
    status: AwgClientStatus = Field(description="running / stopped / pending / error")
    image: str = Field(description="Docker image:tag")
    is_waygate_managed: bool = Field(
        description="True для контейнеров с префиксом `waygate-amnezia-` — это AWG-client'ы Waygate'а",
    )


class ContainerListResponse(BaseModel):
    containers: list[ContainerInfo]


# ############################################
# #  /v1/ipset/apply (custom ipset из CIDR'ов)
# ############################################


class IpsetApplyRequest(BaseModel):
    """Запрос на создание/обновление ipset из явного списка CIDR'ов.

    Идемпотентен: agent делает create -exist + flush + restore (см. geoip.py-pattern).
    """

    name: IpsetName = Field(description="Имя ipset на target")
    cidrs: list[str] = Field(description="Список CIDR/IP")


class IpsetApplyResponse(BaseModel):
    name: str
    cidrs_loaded: int = Field(description="Сколько CIDR'ов загружено")


# ############################################
# #  RoutingDirection (логические маршрутные направления для UI)
# ############################################


class DirectionCreate(BaseModel):
    name: str = Field(description="Человеческое имя направления (например: 'Streaming-EU')")
    awg_client_id: int | None = Field(
        default=None,
        description="ID waygate-amnezia-клиента; null = маршрут без VPN",
    )
    via_interface: InterfaceName = Field(description="Netdev на хосте")
    via_gateway: str = Field(description="IP next-hop'а внутри туннеля")
    geo_list_ids: list[int] = Field(
        default_factory=list,
        description="ID GeoList'ов, чьи ipset'ы попадут в это направление",
    )
    dns_rule_ids: list[int] = Field(
        default_factory=list,
        description="ID DNS-правил",
    )
    ipset_group_ids: list[int] = Field(
        default_factory=list,
        description="ID custom IPset-групп",
    )
    scope: RoutingScope = Field(default=RoutingScope.HOST)
    scope_target: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    is_default_egress: bool = Field(
        default=False,
        description=(
            "Catch-all: «всё что не попало в другие direction'ы → через этот VPN». "
            "Один такой direction на (server, scope) max — control-plane вернёт "
            "409 если уже есть."
        ),
    )


class DirectionUpdate(BaseModel):
    """PATCH — частичное обновление, любое поле можно опустить."""

    name: str | None = None
    awg_client_id: int | None = None
    via_interface: str | None = None
    via_gateway: str | None = None
    geo_list_ids: list[int] | None = None
    dns_rule_ids: list[int] | None = None
    ipset_group_ids: list[int] | None = None
    scope: RoutingScope | None = None
    scope_target: str | None = None
    enabled: bool | None = None
    is_default_egress: bool | None = None


class DirectionResponse(BaseModel):
    id: int
    server_id: int
    awg_client_id: int | None
    name: str
    fwmark: int
    table_id: int
    via_interface: str
    via_gateway: str
    scope: str
    scope_target: str | None
    enabled: bool
    is_default_egress: bool
    geo_list_ids: list[int]
    dns_rule_ids: list[int]
    ipset_group_ids: list[int]
    created_at: datetime
    updated_at: datetime


class DirectionListResponse(BaseModel):
    directions: list[DirectionResponse]


# ############################################
# #  /api/v1/agent-releases (GitHub-прокси)
# ############################################


class AgentRelease(BaseModel):
    """Запись о релизе агента из GitHub Releases."""

    tag: str = Field(description="Git-тег релиза, e.g. agent-v0.2.0")
    version: str = Field(description="Чистая версия без префикса, e.g. 0.2.0")
    name: str = Field(description="Заголовок релиза в GitHub UI")
    published_at: datetime = Field(description="Когда опубликован")
    wheel_url: str = Field(description="URL versionless-wheel'а (waygate_agent-py3-none-any.whl)")


class AgentReleasesResponse(BaseModel):
    releases: list[AgentRelease] = Field(
        description="От самого свежего к старому (UI ставит первый дефолтом).",
    )
