mkdir# Waygate — спецификация проекта

Веб-панель управления GeoIP-маршрутизацией трафика на нескольких Linux-серверах.
Каждый сервер маршрутизирует трафик через AmneziaWG-туннели в зависимости от страны
назначения, домена или ipset-правил. Панель управляет всем парком серверов из одной
точки.

---

## Архитектура

Два сервиса:

- **server** (control plane) — единый бэкенд с веб-интерфейсом. Работает в Docker
  на любом хосте. Хранит состояние в БД, управляет агентами через HTTP, общается
  с фронтендом через REST + WebSocket.
- **agent** — лёгкий демон на каждом управляемом сервере. Запускается как systemd-юнит.
  Управляет ipset, iptables, dnsmasq, AmneziaWG-контейнерами и собственным TLS-сертификатом.
  Не имеет БД, всё состояние применяется командами от server.

Связь панель ↔ агент: HTTP по публичному IP агента (порт 7743 по умолчанию). Авторизация
по Bearer-токену, который генерируется при онбординге. Дополнительный уровень защиты —
iptables-правило на агенте, разрешающее коннект только с IP панели.

Связь фронтенд ↔ панель:
- REST для CRUD-операций
- WebSocket `/ws/events` — общий поток событий (статусы серверов, метрики, применение правил)
- SSE `/api/servers/{id}/provision/stream` — лог онбординга в реальном времени

---

## Структура репозитория

```
waygate/
├── backend/
│   ├── pyproject.toml         # uv workspace: members = ["agent","server","shared"]
│   ├── agent/
│   │   ├── pyproject.toml     # waygate-agent
│   │   ├── Dockerfile         # простой dev-образ, не multi-stage
│   │   ├── main.py            # FastAPI + lifespan + scheduler
│   │   ├── routing.py         # ipset, iptables, ip rule (идемпотентно)
│   │   ├── geoip.py           # скачивание zone-файлов, атомарная замена ipset
│   │   ├── tunnels.py         # парсинг wg show + docker inspect
│   │   ├── dns.py             # генерация конфига dnsmasq, reload
│   │   ├── tls.py             # три режима TLS (upload / path / acme)
│   │   ├── updater.py         # самообновление через wheel + systemctl restart
│   │   └── scheduler.py       # APScheduler: cert renewal, geoip sync, metrics
│   ├── server/
│   │   ├── pyproject.toml     # waygate-server
│   │   ├── main.py            # FastAPI + WebSocket + lifespan
│   │   ├── api/
│   │   │   ├── servers.py     # CRUD серверов + POST /provision
│   │   │   ├── rules.py       # routing rules
│   │   │   ├── geoip.py       # GeoIP списки, sync trigger
│   │   │   ├── dns.py         # DNS-правила
│   │   │   ├── metrics.py     # агрегация метрик от всех агентов
│   │   │   └── tls.py         # настройка TLS на серверах
│   │   ├── provisioner/
│   │   │   ├── ssh.py         # asyncssh обёртка
│   │   │   ├── steps.py       # verify_os, install_deps, deploy_agent
│   │   │   └── bootstrap.py   # запись конфига, systemd unit, healthcheck
│   │   ├── models/
│   │   │   ├── server.py      # Server: host, port, name, token, version, status
│   │   │   ├── rule.py        # RoutingRule
│   │   │   ├── dns_rule.py    # DnsRule
│   │   │   ├── geo_list.py    # GeoList
│   │   │   └── tls_config.py  # TlsConfig
│   │   ├── agent_client/
│   │   │   └── client.py      # aiohttp клиент: status, apply_rules, sync_geoip, ...
│   │   └── ws/
│   │       ├── manager.py     # ConnectionManager: подписки, broadcast
│   │       ├── events.py      # типы событий (Pydantic)
│   │       └── router.py      # WebSocket endpoint
│   └── shared/
│       ├── pyproject.toml     # waygate-shared
│       └── schemas.py         # Pydantic-схемы API агента + StrEnum
├── frontend/                  # см. отдельную секцию
├── deploy/
│   ├── docker-compose.yml     # server + frontend + postgres
│   ├── nginx.conf             # /api → server, /ws → server (Upgrade), / → frontend
│   ├── agent.service          # systemd unit для агента
│   └── renew-hook.sh          # SIGUSR1 для granian после обновления сертификата
└── pyproject.toml             # корневой uv workspace
```

---

## Технологический стек

**Бэкенд (agent + server):**
- Python 3.13
- FastAPI 0.115+
- granian 1.6+ (ASGI-сервер с нативным TLS)
- aiohttp (HTTP-клиент сервер → агенты)
- envparse (конфиг через переменные окружения)
- asyncssh (SSH-онбординг)
- SQLModel + alembic (только в server)
- APScheduler 4.x (только в agent)
- acme.py (Let's Encrypt в agent)

**База данных:**
- SQLite в dev (по умолчанию, через aiosqlite)
- PostgreSQL в prod (через DATABASE_URL=postgresql+asyncpg://...)

**Инструменты:**
- uv — менеджер пакетов и workspace
- ruff — линтер + форматтер
- mypy — статическая типизация
- pre-commit — git-хуки

**Фронтенд:**
- React 18+
- TypeScript
- Vite
- TanStack Query (REST + кеш)
- Zustand (UI state)
- Native WebSocket через кастомный хук

---

## Стиль кода

```
- Не использовать `from __future__ import annotations` — проект на Python 3.13
- Все комментарии, докстринги и тексты — на русском
- Аннотации проставляются везде, кроме возврата в тестах (def test_...)
- Не использовать однобуквенные переменные кроме x, y, i и т.п.
- Все параметры передавать по ключу, кроме общепринятых (max(a,b), sum(c,d))
- Только полные названия: `request`, не `req`; `callback`, не `cb`
- Для перечислений использовать StrEnum (не (str, Enum))
- Использовать StrEnum-константы везде, где строки несут смысловую нагрузку
- Не использовать typing.Union/Optional/List/Dict/Tuple — только встроенный синтаксис:
  x: int | str | None
  items: list[int]
  mapping: dict[str, int]
- Импортировать конечный объект, а не namespace:
  ✓ from asyncpg import Pool
  ✗ import asyncpg; asyncpg.Pool
  Исключения для namespace: status.HTTP_404_NOT_FOUND, Scope.APP — где namespace
  оставляет смысловой контекст
- Описания моделей Pydantic (description=...) на русском
```

Стиль заголовков-секций в коде:

```python
# ############################################
# #  /v1/metrics
# ############################################
```

Стиль комментариев в StrEnum:

```python
class TlsMode(StrEnum):
    UPLOAD = "upload"  # загрузить cert.pem и key.pem через API
    PATH   = "path"    # указать путь к файлам на сервере
    ACME   = "acme"    # получить через Let's Encrypt
```

---

## Контракт API агента — `backend/shared/schemas.py`

```python
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


# ############################################
# #  Перечисления
# ############################################


class TlsMode(StrEnum):
    UPLOAD = "upload"  # загрузить cert.pem и key.pem через API
    PATH   = "path"    # указать путь к файлам на сервере
    ACME   = "acme"    # получить через Let's Encrypt


class AcmeChallenge(StrEnum):
    HTTP01 = "http01"  # HTTP-01: временный endpoint на :80
    DNS01  = "dns01"   # DNS-01: через API DNS-провайдера


class DnsProvider(StrEnum):
    CLOUDFLARE = "cloudflare"  # Cloudflare DNS API
    DESEC      = "desec"       # deSEC (бесплатный, рекомендован)
    ROUTE53    = "route53"     # AWS Route 53


class TunnelStatus(StrEnum):
    UP       = "up"        # туннель работает нормально
    DOWN     = "down"      # туннель недоступен
    DEGRADED = "degraded"  # туннель частично работает (не все пиры)


class UpdateStatus(StrEnum):
    RESTARTING = "restarting"  # агент принял обновление и перезапускается


# ############################################
# #  /v1/status
# ############################################


class AwgContainerInfo(BaseModel):
    name: str      = Field(description="Имя Docker-контейнера AmneziaWG")
    interface: str = Field(description="Сетевой интерфейс (awg0, awg1 ...)")


class AgentStatus(BaseModel):
    version:        str                    = Field(description="Версия агента")
    uptime_seconds: int                    = Field(description="Время работы в секундах")
    hostname:       str                    = Field(description="Имя хоста сервера")
    awg_containers: list[AwgContainerInfo] = Field(description="Обнаруженные AWG-контейнеры")
    rules_applied:  int                    = Field(description="Количество активных правил маршрутизации")
    tls_mode:       TlsMode | None         = Field(default=None, description="Текущий режим TLS")


# ############################################
# #  /v1/metrics
# ############################################


class TunnelMetrics(BaseModel):
    peer:     str        = Field(description="Публичный ключ пира")
    endpoint: str | None = Field(description="Адрес пира (ip:port)")
    rx_bytes: int        = Field(description="Принято байт")
    tx_bytes: int        = Field(description="Передано байт")


class MetricsSnapshot(BaseModel):
    timestamp: datetime            = Field(description="Время снимка метрик")
    tunnels:   list[TunnelMetrics] = Field(description="Метрики по каждому пиру")


# ############################################
# #  /v1/tunnels
# ############################################


class PeerInfo(BaseModel):
    public_key:     str             = Field(description="Публичный ключ пира WireGuard")
    endpoint:       str | None      = Field(description="Адрес пира (ip:port)")
    last_handshake: datetime | None = Field(description="Время последнего handshake")
    rx_bytes:       int             = Field(description="Принято байт")
    tx_bytes:       int             = Field(description="Передано байт")


class TunnelInfo(BaseModel):
    container_name: str            = Field(description="Имя Docker-контейнера")
    interface:      str            = Field(description="Сетевой интерфейс")
    peers:          list[PeerInfo] = Field(description="Список пиров")
    status:         TunnelStatus   = Field(description="Состояние туннеля")


class TunnelsResponse(BaseModel):
    tunnels: list[TunnelInfo] = Field(description="Все AWG-туннели на сервере")


# ############################################
# #  /v1/rules/apply
# ############################################


class RoutingRule(BaseModel):
    country:       str  = Field(description="Код страны ISO 3166-1 alpha-2 (RU, BY ...)")
    ipset_name:    str  = Field(description="Имя ipset-множества (russia, belarus ...)")
    fwmark:        int  = Field(description="Метка пакетов для policy routing")
    table_id:      int  = Field(description="Номер таблицы маршрутизации")
    via_interface: str  = Field(description="Исходящий интерфейс (awg0 ...)")
    via_gateway:   str  = Field(description="IP-адрес шлюза")
    enabled:       bool = Field(description="Активно ли правило")


class ApplyRulesRequest(BaseModel):
    rules: list[RoutingRule] = Field(description="Желаемое состояние правил — агент применяет diff")


class ApplyRulesResponse(BaseModel):
    applied: int       = Field(description="Добавлено или изменено правил")
    skipped: int       = Field(description="Правил без изменений")
    errors:  list[str] = Field(default_factory=list, description="Ошибки применения")


# ############################################
# #  /v1/geoip/sync
# ############################################


class GeoIpSyncRequest(BaseModel):
    country:      str       = Field(description="Код страны (RU, BY ...)")
    ipset_name:   str       = Field(description="Имя ipset-множества")
    source_url:   str       = Field(description="URL zone-файла (ipdeny или RIPE)")
    custom_cidrs: list[str] = Field(default_factory=list, description="Дополнительные CIDR-блоки")


class GeoIpSyncResponse(BaseModel):
    cidrs_loaded: int = Field(description="Загружено CIDR-блоков")
    ipset_name:   str = Field(description="Имя применённого ipset")
    duration_ms:  int = Field(description="Время выполнения в миллисекундах")


# ############################################
# #  /v1/dns/apply
# ############################################


class DnsRule(BaseModel):
    name:       str       = Field(description="Название группы доменов")
    domains:    list[str] = Field(description="Домены — dnsmasq пишет резолвы в ipset")
    ipset_name: str       = Field(description="Имя ipset для резолвов")


class ApplyDnsRequest(BaseModel):
    rules: list[DnsRule] = Field(description="Желаемое состояние DNS-правил — агент применяет diff")


class ApplyDnsResponse(BaseModel):
    applied: int       = Field(description="Применено правил")
    errors:  list[str] = Field(default_factory=list, description="Ошибки применения")


# ############################################
# #  /v1/tls/apply
# ############################################


class TlsConfig(BaseModel):
    mode: TlsMode = Field(description="Режим получения сертификата")
    port: int     = Field(default=7743, description="Порт агента")

    # mode = upload
    cert_pem: str | None = Field(default=None, description="Содержимое cert.pem (base64)")
    key_pem:  str | None = Field(default=None, description="Содержимое key.pem (base64)")

    # mode = path
    cert_path: str | None = Field(default=None, description="Путь к cert.pem на сервере")
    key_path:  str | None = Field(default=None, description="Путь к key.pem на сервере")

    # mode = acme
    domains:      list[str]            = Field(default_factory=list, description="Домены для сертификата")
    email:        str | None           = Field(default=None, description="Email для Let's Encrypt")
    challenge:    AcmeChallenge | None = Field(default=None, description="Тип ACME-challenge")
    dns_provider: DnsProvider | None   = Field(default=None, description="DNS-провайдер для DNS-01")
    dns_api_key:  str | None           = Field(default=None, description="API-ключ DNS-провайдера")

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
            if self.challenge is AcmeChallenge.DNS01:
                if not self.dns_provider or not self.dns_api_key:
                    raise ValueError("challenge=dns01 требует dns_provider и dns_api_key")
        return self


class TlsApplyResponse(BaseModel):
    cert_path:  str       = Field(description="Путь к применённому сертификату на сервере")
    expires_at: datetime  = Field(description="Дата истечения сертификата")
    domains:    list[str] = Field(description="Домены в сертификате")


# ############################################
# #  /v1/update
# ############################################


class UpdateRequest(BaseModel):
    version:   str = Field(description="Целевая версия агента (например 0.2.0)")
    wheel_url: str = Field(description="URL wheel-файла на GitHub Releases")


class UpdateResponse(BaseModel):
    previous_version: str          = Field(description="Версия агента до обновления")
    status:           UpdateStatus = Field(description="Статус — всегда restarting")


# ############################################
# #  /v1/token/rotate
# ############################################


class TokenRotateResponse(BaseModel):
    token: str = Field(description="Новый Bearer-токен для аутентификации")
```

---

## Агент

### Эндпоинты (все под `/v1/`)

Все запросы требуют заголовок `Authorization: Bearer <token>`. Токен — 64-байтный
случайный, генерируется при онбординге, хранится в `/etc/waygate/agent.env`.

| Метод | Путь | Запрос | Ответ |
|-------|------|--------|-------|
| GET   | `/v1/status`        | —                  | `AgentStatus` |
| GET   | `/v1/metrics`       | —                  | `MetricsSnapshot` |
| GET   | `/v1/tunnels`       | —                  | `TunnelsResponse` |
| POST  | `/v1/rules/apply`   | `ApplyRulesRequest` | `ApplyRulesResponse` |
| POST  | `/v1/geoip/sync`    | `GeoIpSyncRequest`  | `GeoIpSyncResponse` |
| POST  | `/v1/dns/apply`     | `ApplyDnsRequest`   | `ApplyDnsResponse` |
| POST  | `/v1/tls/apply`     | `TlsConfig`         | `TlsApplyResponse` |
| POST  | `/v1/update`        | `UpdateRequest`     | `UpdateResponse` |
| POST  | `/v1/token/rotate`  | —                  | `TokenRotateResponse` |

### Принципы реализации

- **Идемпотентность**. Все `apply`-эндпоинты делают diff текущего состояния и желаемого,
  применяют только разницу. Никаких `flush`-ов — трафик не должен прерываться.
- **Атомарность ipset**. Замена через `ipset restore` со swap — старый и новый ipset
  существуют одновременно, переключение мгновенное.
- **Все системные команды через `asyncio.create_subprocess_exec`** — не блокируем event loop.
- **Логирование** через `loguru`, в stdout (systemd подхватывает в journald).

### TLS — три режима в `tls.py`

Реализуется через выбор в `TlsConfig.mode`:

- **upload**: cert.pem и key.pem передаются в base64 в теле запроса. Агент пишет
  в `/etc/waygate/tls/cert.pem` и `/etc/waygate/tls/key.pem`, делает reload granian
  через SIGUSR1.
- **path**: агент использует существующие файлы по указанному пути (например,
  `/etc/letsencrypt/live/domain/`). При reload-сигнале перечитывает с диска.
- **acme**: встроенный ACME-клиент через `acme.py`.
  - HTTP-01: агент временно поднимает endpoint `/.well-known/acme-challenge/<token>` на :80.
  - DNS-01: использует API DNS-провайдера (Cloudflare, deSEC, Route53) для создания TXT-записи.
  - После получения сертификата сохраняет в `/etc/waygate/tls/`, обновляет ссылки.

### Self-update — `updater.py`

```
1. Сервер вызывает POST /v1/update с {version, wheel_url}
2. Агент скачивает wheel в /tmp/waygate-agent-{version}.whl
3. Агент запускает: pip install --upgrade /tmp/waygate-agent-{version}.whl
   (через uv pip install в своём venv)
4. Агент возвращает UpdateResponse{previous_version, status=restarting}
5. Агент вызывает: systemctl restart waygate-agent (через subprocess)
6. Сервер ждёт reconnect, проверяет GET /v1/status пока version не совпадёт
   с целевой
```

### Scheduler — `scheduler.py`

APScheduler 4.x AsyncIOScheduler внутри процесса агента (без системного cron):

```python
scheduler.add_schedule(check_cert_expiry,  IntervalTrigger(hours=24))
scheduler.add_schedule(sync_geoip_lists,   IntervalTrigger(days=7))
scheduler.add_schedule(collect_metrics,    IntervalTrigger(seconds=30))
```

`check_cert_expiry` для acme-режима: если до истечения сертификата меньше 30 дней —
триггер renewal. После успешного обновления — SIGUSR1 на главный процесс granian
для перечитывания TLS без даунтайма.

`collect_metrics` пишет в in-memory ring buffer (последние 60 точек × 30 сек = 30 минут).
Сервер опрашивает `GET /v1/metrics` каждые 30 секунд и сохраняет в свою БД историю.

### Конфигурация (envparse)

`/etc/waygate/agent.env`:

```
PORT=7743
TOKEN=<64-byte random>
LOG_LEVEL=INFO
TLS_DIR=/etc/waygate/tls
DATA_DIR=/var/lib/waygate-agent
```

```python
from envparse import env

class Settings:
    port:      int = env.int("PORT", default=7743)
    token:     str = env.str("TOKEN")
    log_level: str = env.str("LOG_LEVEL", default="INFO")
    tls_dir:   str = env.str("TLS_DIR", default="/etc/waygate/tls")
    data_dir:  str = env.str("DATA_DIR", default="/var/lib/waygate-agent")
```

### Запуск

```bash
granian --interface asgi waygate_agent.main:app \
  --host 0.0.0.0 --port $PORT \
  --ssl-certificate $TLS_DIR/cert.pem \
  --ssl-keyfile $TLS_DIR/key.pem
```

---

## Сервер (control plane)

### REST API (под `/api/v1/`)

#### Серверы — `/api/v1/servers`

```
GET    /api/v1/servers                       — список всех серверов
POST   /api/v1/servers/provision             — добавить новый сервер (онбординг)
GET    /api/v1/servers/{id}                  — детали сервера
DELETE /api/v1/servers/{id}                  — удалить сервер
GET    /api/v1/servers/{id}/provision/stream — SSE поток лога онбординга
POST   /api/v1/servers/{id}/update           — запустить обновление агента
POST   /api/v1/servers/{id}/token/rotate     — ротация токена
```

#### Правила маршрутизации — `/api/v1/rules`

```
GET    /api/v1/servers/{id}/rules           — список правил для сервера
POST   /api/v1/servers/{id}/rules           — создать правило
PATCH  /api/v1/servers/{id}/rules/{rid}     — изменить (включить/выключить, изменить via)
DELETE /api/v1/servers/{id}/rules/{rid}     — удалить
POST   /api/v1/servers/{id}/rules/apply     — применить все правила к агенту
```

#### GeoIP — `/api/v1/geoip`

```
GET    /api/v1/geoip/lists                   — все GeoIP-списки
POST   /api/v1/geoip/lists                   — добавить новый (страна + URL)
DELETE /api/v1/geoip/lists/{id}              — удалить
POST   /api/v1/servers/{id}/geoip/sync       — триггер синхронизации на агенте
```

#### DNS — `/api/v1/dns`

```
GET    /api/v1/servers/{id}/dns               — список DNS-правил
POST   /api/v1/servers/{id}/dns               — создать
PATCH  /api/v1/servers/{id}/dns/{rid}         — изменить
DELETE /api/v1/servers/{id}/dns/{rid}         — удалить
POST   /api/v1/servers/{id}/dns/apply         — применить
```

#### Метрики — `/api/v1/metrics`

```
GET /api/v1/servers/{id}/metrics?range=1h|6h|24h — исторические метрики из БД
```

Возвращает временной ряд `{timestamp, rx_bytes, tx_bytes}` для графика.

#### TLS — `/api/v1/tls`

```
GET  /api/v1/servers/{id}/tls                 — текущая конфигурация TLS
POST /api/v1/servers/{id}/tls                 — применить новую (TlsConfig)
```

### Provisioner (онбординг через asyncssh)

`server/provisioner/steps.py` — последовательность шагов. Каждый шаг эмитит событие
в SSE-стрим, фронтенд видит лог в реальном времени.

```python
async def provision(host, port, user, auth, *, sse_emit):
    async with await asyncssh.connect(host, port=port, ...) as connection:
        await sse_emit(message="connect ok")

        # Шаг 1: проверка ОС
        await verify_os(connection=connection, sse_emit=sse_emit)

        # Шаг 2: установка зависимостей
        await install_deps(connection=connection, sse_emit=sse_emit)
        # apt install -y ipset iptables iproute2 dnsmasq

        # Шаг 3: проверка AmneziaWG-контейнеров
        containers = await detect_awg_containers(connection=connection)
        await sse_emit(message=f"awg containers: {containers}")

        # Шаг 4: установка агента
        token = secrets.token_urlsafe(48)
        await deploy_agent(connection=connection, token=token, sse_emit=sse_emit)
        # - скачивание wheel с GitHub Releases
        # - uv venv в /opt/waygate-agent
        # - uv pip install wheel
        # - запись /etc/waygate/agent.env
        # - копирование agent.service в /etc/systemd/system/
        # - systemctl daemon-reload && enable && start

        # Шаг 5: первый healthcheck
        await wait_for_agent(host=host, port=7743, token=token, sse_emit=sse_emit)

        # Шаг 6: запись в БД
        return Server(host=host, port=7743, token=token, ...)
```

### Agent client — `server/agent_client/client.py`

Aiohttp-обёртка с типизированными методами под schemas:

```python
class AgentClient:
    def __init__(self, *, host: str, port: int, token: str):
        self._base_url = f"https://{host}:{port}/v1"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def status(self) -> AgentStatus: ...
    async def metrics(self) -> MetricsSnapshot: ...
    async def tunnels(self) -> TunnelsResponse: ...
    async def apply_rules(self, *, request: ApplyRulesRequest) -> ApplyRulesResponse: ...
    async def sync_geoip(self, *, request: GeoIpSyncRequest) -> GeoIpSyncResponse: ...
    async def apply_dns(self, *, request: ApplyDnsRequest) -> ApplyDnsResponse: ...
    async def apply_tls(self, *, config: TlsConfig) -> TlsApplyResponse: ...
    async def update(self, *, request: UpdateRequest) -> UpdateResponse: ...
```

Timeout: connect=5с, read=60с (apply-команды могут идти долго). Retry — 3 попытки
с экспоненциальным backoff через tenacity (только для GET-запросов).

### WebSocket — `/ws/events`

Авторизация через query-параметр: `/ws/events?token=<jwt>`.

Типы событий (`server/ws/events.py`):

```python
class EventType(StrEnum):
    SERVER_STATUS_CHANGED = "server.status_changed"  # online/offline/degraded
    SERVER_METRICS        = "server.metrics"         # новые точки rx/tx
    SERVER_AGENT_UPDATED  = "server.agent_updated"   # агент обновился
    RULE_APPLIED          = "rule.applied"           # правило применено
    DNS_APPLIED           = "dns.applied"
    GEOIP_SYNCED          = "geoip.synced"
    TLS_APPLIED           = "tls.applied"
    PROVISION_PROGRESS    = "provision.progress"     # для активного онбординга


class WsEvent(BaseModel):
    type:    EventType
    payload: dict
    server_id: int | None = None
    timestamp: datetime
```

Паттерн broadcast:

```python
class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def broadcast(self, *, event: WsEvent) -> None:
        # рассылка всем подключённым клиентам
        ...
```

Где эмитить события:
- После любого `apply_*` в API → broadcast соответствующего события
- В background-таске метрик (опрашиваем агенты каждые 30с) → SERVER_METRICS
- В таске healthcheck (каждые 60с) → SERVER_STATUS_CHANGED при изменении

### Модели БД (SQLModel + alembic)

```python
class Server(SQLModel, table=True):
    id:              int | None       = Field(default=None, primary_key=True)
    host:            str
    port:            int              = Field(default=7743)
    name:            str
    token:           str              # Bearer-токен агента
    version:         str              # текущая версия агента
    status:          str              # online / offline / degraded
    region:          str | None       = None  # для группировки в sidebar
    awg_containers:  list[str]        = Field(sa_type=JSON, default_factory=list)
    added_at:        datetime         = Field(default_factory=datetime.now)
    last_seen_at:    datetime | None  = None


class RoutingRule(SQLModel, table=True):
    id:            int | None  = Field(default=None, primary_key=True)
    server_id:     int         = Field(foreign_key="server.id")
    country:       str
    ipset_name:    str
    fwmark:        int
    table_id:      int
    via_interface: str
    via_gateway:   str
    enabled:       bool        = True


class DnsRule(SQLModel, table=True):
    id:         int | None = Field(default=None, primary_key=True)
    server_id:  int        = Field(foreign_key="server.id")
    name:       str
    domains:    list[str]  = Field(sa_type=JSON)
    ipset_name: str
    enabled:    bool       = True


class GeoList(SQLModel, table=True):
    id:             int | None      = Field(default=None, primary_key=True)
    country:        str
    name:           str              # human-readable
    source_url:     str              # ipdeny URL
    ipv4_count:     int              = 0
    ipv6_count:     int              = 0
    custom_count:   int              = 0
    last_synced_at: datetime | None  = None
    status:         str              = "stale"  # synced / stale / error


class TlsConfigRow(SQLModel, table=True):
    __tablename__ = "tls_config"
    id:         int | None       = Field(default=None, primary_key=True)
    server_id:  int              = Field(foreign_key="server.id", unique=True)
    config:     dict             = Field(sa_type=JSON)  # сериализованный TlsConfig
    expires_at: datetime | None  = None


class MetricsPoint(SQLModel, table=True):
    __tablename__ = "metrics_points"
    id:         int | None  = Field(default=None, primary_key=True)
    server_id:  int         = Field(foreign_key="server.id", index=True)
    timestamp:  datetime    = Field(index=True)
    rx_bytes:   int
    tx_bytes:   int
```

Для метрик стоит подумать о retention — старше 30 дней удалять.

### Конфигурация (envparse)

```python
from envparse import env

class Settings:
    db_url:        str = env.str("DATABASE_URL", default="sqlite+aiosqlite:///waygate.db")
    secret_key:    str = env.str("SECRET_KEY")  # для подписи JWT WebSocket-токенов
    port:          int = env.int("PORT", default=8000)
    cors_origins:  list[str] = env.list("CORS_ORIGINS", default=["http://localhost:5173"])
    log_level:     str = env.str("LOG_LEVEL", default="INFO")
    metrics_poll_seconds: int = env.int("METRICS_POLL_SECONDS", default=30)
    healthcheck_seconds:  int = env.int("HEALTHCHECK_SECONDS", default=60)
```

---

## Фронтенд

В `frontend/` уже есть готовый дизайн от Claude Design — файлы лежат в архиве,
структура такая:

```
data.jsx        — моки (заменить на TanStack Query)
primitives.jsx  — Icon, Badge, Toggle, MonoPill, ViaPill, IconTile, Sparkline,
                  Metric, SectionHead
shell.jsx       — Sidebar, Topbar, Tabs
tab-routing.jsx — RoutingTab
tab-tunnels.jsx — TunnelsTab
tab-dns.jsx     — DnsTab
tab-geoip.jsx   — GeoIpTab
tab-metrics.jsx — MetricsTab + Chart
add-server.jsx  — AddServerModal (4-шаговый wizard)
tls-modal.jsx   — TlsModal (3 режима)
app.jsx         — корневой App
styles.css      — все стили (готов к использованию)
Waygate.html    — точка входа для preview
```

### Структура production-фронтенда

Дизайн нужно перенести в нормальный Vite-проект:

```
frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx              — точка входа
    ├── App.tsx               — корневой компонент (адаптация app.jsx)
    ├── styles.css            — копия styles.css из дизайна
    ├── api/
    │   ├── client.ts         — fetch-обёртка с базовым URL и токеном
    │   ├── types.ts          — автогенерация из openapi.json
    │   ├── servers.ts        — useServers, useProvision, useDeleteServer
    │   ├── rules.ts          — useRules, useApplyRules
    │   ├── dns.ts            — useDnsRules, useApplyDns
    │   ├── geoip.ts          — useGeoIpLists, useSyncGeoIp
    │   └── metrics.ts        — useMetrics(serverId, range)
    ├── ws/
    │   ├── useWS.ts          — хук подключения, автореконнект
    │   └── store.ts          — zustand: connection status, активные подписки
    ├── store/
    │   └── ui.ts             — zustand: activeServerId, activeTab, modals
    ├── components/
    │   ├── primitives/       — Icon, Badge, Toggle, MonoPill, IconTile, Sparkline
    │   ├── Sidebar.tsx
    │   ├── Topbar.tsx
    │   ├── Tabs.tsx
    │   ├── Metric.tsx
    │   ├── SectionHead.tsx
    │   └── StatusBar.tsx
    ├── pages/
    │   ├── RoutingTab.tsx
    │   ├── TunnelsTab.tsx
    │   ├── DnsTab.tsx
    │   ├── GeoIpTab.tsx
    │   └── MetricsTab.tsx
    └── modals/
        ├── AddServerModal.tsx
        └── TlsModal.tsx
```

### Что нужно сделать при переносе из дизайна

1. **Конвертировать window-globals в ES-модули**.
   В дизайне: `window.RoutingTab = RoutingTab`.
   В проде: `export function RoutingTab(...) {...}` + соответствующий импорт.

2. **Заменить моки на TanStack Query**.
   В дизайне: `const { SERVERS } = window.WG_DATA`.
   В проде:
   ```typescript
   const { data: servers } = useQuery({
     queryKey: ["servers"],
     queryFn: () => api.servers.list(),
   });
   ```

3. **Подключить WebSocket для live-обновлений**.
   После любого WS-события вызвать `queryClient.invalidateQueries(["servers"])`
   или соответствующий ключ — TanStack сам перезапросит данные.

4. **Сгенерировать TypeScript-типы из OpenAPI**:
   ```bash
   npx openapi-typescript http://localhost:8000/openapi.json -o src/api/types.ts
   ```
   После любого изменения схемы на бэкенде — перегенерировать.

5. **Адаптировать AddServerModal**: сейчас лог онбординга мокается через setTimeout.
   Заменить на реальный SSE через `EventSource`:
   ```typescript
   const es = new EventSource(`/api/v1/servers/${id}/provision/stream`);
   es.onmessage = (e) => setLines(prev => [...prev, JSON.parse(e.data)]);
   ```

6. **Адаптировать MetricsTab/Chart**: график рисует моки (`Math.sin`).
   Заменить на реальные данные из `useMetrics(serverId, range)`.

7. **CSS оставить как есть** — `styles.css` из дизайна копируется в `src/styles.css`
   и импортируется в `main.tsx`. Темы (`data-theme="dark"`) переключаются через
   атрибут на `<html>`.

8. **TweaksPanel из дизайна не переносить** — это утилита Claude Design для
   live-редактирования прототипа, в проде не нужна.

### Зависимости

```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "@tanstack/react-query": "^5.0.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "typescript": "^5.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "openapi-typescript": "^7.0.0"
  }
}
```

### vite.config.ts

```typescript
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws":  { target: "ws://localhost:8000", ws: true },
    },
  },
});
```

---

## Деплой

### Docker Compose

`deploy/docker-compose.yml` — три сервиса:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: waygate
      POSTGRES_USER: waygate
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data

  server:
    build: ../backend
    command: granian --interface asgi waygate_server.main:app --host 0.0.0.0 --port 8000
    environment:
      DATABASE_URL: postgresql+asyncpg://waygate:${POSTGRES_PASSWORD}@postgres/waygate
      SECRET_KEY: ${SECRET_KEY}
    depends_on: [postgres]

  frontend:
    build: ../frontend
    # nginx со статикой

  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "443:443"
    depends_on: [server, frontend]
```

### Systemd unit для агента

`deploy/agent.service`:

```ini
[Unit]
Description=Waygate agent
After=network.target docker.service

[Service]
Type=simple
EnvironmentFile=/etc/waygate/agent.env
ExecStart=/opt/waygate-agent/.venv/bin/granian \
  --interface asgi waygate_agent.main:app \
  --host 0.0.0.0 --port ${PORT} \
  --ssl-certificate ${TLS_DIR}/cert.pem \
  --ssl-keyfile ${TLS_DIR}/key.pem
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Порядок реализации

### Фаза 1 — фундамент (1-2 дня)

- [x] Контракт схем — `backend/shared/schemas.py` (готов выше)
- [ ] Структура монорепо: создать `backend/`, `backend/agent/`, `backend/server/`,
      `backend/shared/`, `frontend/`, `deploy/`
- [ ] Корневой `pyproject.toml` с uv workspace
- [ ] `pyproject.toml` для каждого пакета
- [ ] Конфиги ruff, mypy, pre-commit

### Фаза 2 — агент MVP (3-4 дня)

- [ ] `agent/main.py` — FastAPI app с lifespan, scheduler
- [ ] `agent/routing.py` — apply rules идемпотентно (ipset, iptables, ip rule)
- [ ] `agent/geoip.py` — скачивание zone-файлов, атомарная замена ipset
- [ ] `agent/tunnels.py` — парсинг wg show + docker inspect
- [ ] `agent/main.py` — все эндпоинты `/v1/*` (status, metrics, tunnels, rules, geoip)
- [ ] Bearer-аутентификация через middleware
- [ ] Dockerfile для dev-запуска

### Фаза 3 — сервер MVP (3-4 дня)

- [ ] `server/main.py` — FastAPI app с lifespan
- [ ] `server/models/` — все SQLModel модели
- [ ] alembic init + первая миграция
- [ ] `server/agent_client/` — aiohttp клиент с типами
- [ ] `server/api/servers.py` — CRUD серверов (без онбординга пока)
- [ ] `server/api/rules.py` — CRUD правил
- [ ] `server/api/geoip.py` — CRUD GeoIP-списков
- [ ] `server/api/dns.py` — CRUD DNS-правил
- [ ] `server/api/metrics.py` — выдача исторических метрик
- [ ] Background-таска: опрос метрик с агентов каждые 30 сек

### Фаза 4 — онбординг (2-3 дня)

- [ ] `server/provisioner/ssh.py` — asyncssh обёртка
- [ ] `server/provisioner/steps.py` — verify_os, install_deps, deploy_agent, healthcheck
- [ ] `server/api/servers.py` — `POST /provision` + `GET /provision/stream` (SSE)
- [ ] Сборка wheel агента в CI на GitHub Releases (для скачивания при онбординге)

### Фаза 5 — WebSocket (1-2 дня)

- [ ] `server/ws/events.py` — типы событий
- [ ] `server/ws/manager.py` — ConnectionManager
- [ ] `server/ws/router.py` — endpoint `/ws/events` с JWT-авторизацией
- [ ] Эмиссия событий из всех api-роутеров после успешных операций

### Фаза 6 — TLS и обновления (2-3 дня)

- [ ] `agent/tls.py` — три режима, валидация TlsConfig
- [ ] `agent/scheduler.py` — auto-renew для acme
- [ ] `agent/updater.py` — self-update через wheel + restart
- [ ] `server/api/tls.py` — настройка TLS
- [ ] `server/api/servers.py` — `POST /update` для обновления агента

### Фаза 7 — фронтенд (3-5 дней)

- [ ] Vite + TypeScript + React 18
- [ ] Перенос дизайна — каждый файл из дизайна → модульный компонент
- [ ] TanStack Query: подключить ко всем экранам
- [ ] WebSocket-хук + invalidateQueries по событиям
- [ ] SSE для онбординга в AddServerModal
- [ ] Реальные данные в Chart (MetricsTab)
- [ ] Автогенерация TypeScript-типов из openapi.json

### Фаза 8 — деплой (1 день)

- [ ] `deploy/docker-compose.yml`
- [ ] `deploy/nginx.conf`
- [ ] `deploy/agent.service`
- [ ] README с инструкцией установки

---

## Дополнительные требования

- **Логирование**: loguru везде, в stdout
- **Тесты**: pytest + pytest-asyncio. Минимально — на provisioner (моки asyncssh)
  и на routing.py (моки subprocess). API-тесты через httpx AsyncClient.
- **Версионирование**: SemVer. Релизы агента — теги `agent-v0.1.0`,
  релизы сервера — `server-v0.1.0`. Wheel'ы агента публикуются в GitHub Releases.
- **CI**: GitHub Actions — ruff, mypy, pytest на каждый PR. Сборка wheel агента
  при релиз-теге.
