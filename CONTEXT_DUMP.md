# Waygate — полный контекст проекта

> Самодостаточный контекст-дамп для онбординга AI-ассистента с нуля.
> Отражает реальное текущее состояние (PROJECT_STATE + ROUTING_ARCHITECTURE),
> а не исходный «как задумывалось» SPEC, где есть расхождения.

## Что это

Веб-панель управления **GeoIP-маршрутизацией трафика** на парке Linux-серверов. Каждый
managed-сервер маршрутизирует трафик через **AmneziaWG-туннели** в зависимости от страны
назначения, домена или ipset-правил (custom CIDR-списки). Панель управляет всем парком из
одной точки.

**Реальный use-case:** клиент (телефон/мак/iPad) с AmneziaVPN-конфигом подключается к
target-серверу, на котором стоит **AWG-server-контейнер** (вне Waygate, ставит оператор).
Forwarded-трафик клиента попадает в host netns target'а, и Waygate маршрутизирует его через
**AWG-client-контейнеры** (уже Waygate-managed) — на выход с нужным гео-IP (RU/NL/etc).

## Архитектура: три бэкенд-сервиса + фронт

- **`backend/agent`** (`waygate-agent`) — лёгкий FastAPI-демон на каждом managed-сервере,
  systemd-юнит. Идемпотентный apply на ipset/iptables/ip rule/ip route, GeoIP atomic swap,
  dnsmasq, управление AWG-контейнерами, self-update через wheel + `systemctl restart`,
  scheduler на APScheduler 4. **Без БД** — всё состояние применяется командами от server.
  Текущая версия ~0.2.32.
- **`backend/server`** (`waygate-server`) — control-plane: FastAPI + SQLModel + alembic. CRUD
  ресурсов, `agent_client` на aiohttp+tenacity, фоновый параллельный metrics-poller +
  healthcheck, WebSocket `/ws/events`, SSE для онбординга, JWT для WS. Версия ~0.1.2.
- **`backend/shared`** (`waygate-shared`) — Pydantic-схемы (контракт agent↔server), парсер
  AmneziaWG `.conf` (`awg_config.py`), формулы именования iface/контейнеров (`awg_naming.py` —
  единая для agent/server/frontend).
- **`frontend`** — Vite + React 18 + TypeScript + TanStack Query + Zustand. WS-хук с
  auto-reconnect и `invalidateQueries` по событиям, SSE для live-лога онбординга.

**Связь панель↔агент:** HTTP по публичному IP агента (порт 7743), Bearer-токен (генерируется
при онбординге), + iptables-allowlist по IP панели.
**Связь фронт↔панель:** REST (CRUD) + WebSocket `/ws/events` (статусы/метрики/apply) + SSE
`/api/v1/servers/{id}/provision/stream` (лог онбординга).

## Статус: все 8 фаз SPEC закрыты + большой техдолг

Готово: фундамент (uv workspace, ruff/mypy), агент MVP, сервер MVP, онбординг (asyncssh+SSE),
WebSocket+JWT, TLS+APScheduler+self-update, фронт, деплой (docker-compose+nginx+systemd),
CI/CD. Сверх того закрыто: **auth** (username/password → bcrypt + session-JWT, первый админ из
ENV `WAYGATE_ADMIN_USER`/`WAYGATE_ADMIN_PASSWORD`), audit-log, prometheus-метрики, security
headers, rate limit, e2e Playwright, редизайн routing-directions, integration-тесты с реальным
Docker-контейнером, и серия apply-flow фиксов (NFT-1..8).

**Тесты:** 218 backend unit + 11+ e2e зелёные. +16 integration с реальным `--privileged`-
контейнером (отключены в `addopts`, гоняются явно `uv run pytest -m integration`, ~60-90с —
покрывают ipset/dns/routing.apply + recovery race + mangle-recovery + ssh-провижионер).
Integration используют `nft list chain` для проверки реального netfilter-state. ruff/format/
mypy чисто, frontend typecheck/build зелёные, docker compose поднимается за ~12с.

## Ключевая архитектура: Routing Directions (после редизайна)

Центральная модель — **`RoutingDirection` (header)** → N child-**`RoutingRule`** с общим
`fwmark`/`table_id`/`via_*`.
Direction = «трафик из {GeoIP-зон, DNS-правил, IPset-групп} через VPN-клиента X». Один Direction
объединяет несколько источников под одной fwmark.

- `_materialize_rules()` — создаёт по одному child-RoutingRule на каждый ref с общей fwmark.
- `direction_sources` pivot-таблица `(direction_id, source_type, source_id)` — типизированный
  reverse-lookup (заменил старый string-matching).
- `RoutingRule.direction_id` — `ON DELETE CASCADE` прописан и в `sa_column` модели, и в alembic
  (нужно чтобы каскад работал и в SQLite-тестах, и в Postgres).
- `IpsetGroup` — третья сущность: custom CIDR-списки без GeoIP/DNS, agentский
  `apply_custom_ipset()` с atomic-swap.
- Catch-all direction через `is_default_egress`.
- Direction CRUD-update **перематериализует child-правила целиком** (delete+insert) при
  изменении любого из `geo_list_ids`/`dns_rule_ids`/`ipset_group_ids`/`via_interface`/
  `via_gateway`/`scope`/`scope_target`/`enabled` — diff не делаем намеренно.

### Два scope'а Direction'а

- **scope=host** — AWG-server в `--network host`. Forwarded-трафик в host netns, mangle/route
  там же.
- **scope=container** — AWG-server в bridge/своей netns. Агент переключает AWG-client в netns
  target'а через `--network container:<имя>`, mangle/route применяет через `nsenter`. **Один
  AWG-client = одна netns** одновременно (нельзя параллельно в host- и container-direction'ах).

### Цепочка netfilter (критично — НЕ патчить инкрементально)

`_MARK_CHAINS = ("PREROUTING",)` — **только PREROUTING**, не FORWARD (mark после route lookup
не вызывает reroute) и не OUTPUT (socket-bind mismatch ломает local TCP). Полный путь:

```
mangle PREROUTING:
  1. addrtype --dst-type LOCAL → RETURN     (защита SSH/agent/handshake на свой IP)
  2. ESTABLISHED → RETURN                    (reply'и не маркируются — conntrack ведёт)
  5. match-set <ipset> dst → MARK <fwmark>   (forwarded → VPN)
ip rule fwmark X lookup table Y → table Y: default dev awg-<client>  (БЕЗ via — туннели POINTOPOINT)
nat POSTROUTING: -o awg-<client> -j MASQUERADE
mangle POSTROUTING: TCPMSS --clamp-mss-to-pmtu (без -o фильтра)
```

### Известные ограничения routing'а

- scope=host + AWG-server в `--network bridge` = двойной NAT через docker → conntrack ломается
  → 0 b/s. Решение: `--network host` или scope=container.
- Local TCP с самого target'а через mark-routing не работает (socket-bind mismatch). С 0.2.28
  OUTPUT-mark выключен, local TCP идёт через eth0 без VPN.
- **Ubuntu 24.04 + Docker 28+:** команды `iptables` попадают в shadow-chain через iptables-nft
  compat и НЕ срабатывают (реальная Docker-managed `chain ip filter FORWARD` имеет policy drop).
  Решение — писать через `nft` напрямую. Агент ставит `update-alternatives --set iptables
  iptables-nft` + `_recover_mangle_if_incompatible` авто-flush + MTU=1280 на AWG-iface (двойной
  туннель не фрагментируется).

## API-контракт агента (`/v1/`, Bearer-токен)

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/v1/status` | `AgentStatus` |
| GET | `/v1/metrics` | `MetricsSnapshot` (ring buffer) |
| GET | `/v1/tunnels` | `TunnelsResponse` |
| POST | `/v1/rules/apply` | `ApplyRulesResponse` (diff-based) |
| POST | `/v1/geoip/sync` | `GeoIpSyncResponse` (ipset atomic swap) |
| POST | `/v1/dns/apply` | `ApplyDnsResponse` |
| POST | `/v1/tls/apply` | `TlsApplyResponse` |
| POST | `/v1/update` | self-update |
| POST | `/v1/token/rotate` | новый токен |

Принципы: идемпотентность (diff, без flush — трафик не прерывается), атомарность ipset
(`ipset restore` + `swap`), все системные команды через `asyncio.create_subprocess_exec`,
loguru в stdout.

**TLS три режима:** `upload` (base64 cert/key + SIGUSR1), `path` (симлинки на существующие
файлы), `acme` — **скелет, не реализовано** (полный ACME-flow отложен в backlog).

## API-контракт сервера (`/api/v1/`)

Всё под global dependency `Depends(require_user)` кроме `/auth/login`. EventSource (SSE) не
ставит header → `require_user` принимает `?access_token=` query-param fallback.

- `/auth/login`, `/me`, `/logout`
- `/servers` (+ `/provision`, `/provision/stream` SSE, `/update`, `/token/rotate`)
- `/directions`, `/ipset_groups`, `/clients` (AWG), `/rules`
- `/dns`, `/geoip`, `/metrics`, `/tls`, `/audit`
- `/agent-releases` — GitHub-прокси с 5-мин кешем (фильтр тегов `agent-v*`, для UI dropdown
  версий)

**WS события (`EventType`):** server.status_changed / server.metrics / server.agent_updated /
rule.applied / dns.applied / geoip.synced / tls.applied / provision.progress / direction.* /
ipset_groups.*

**Модели БД:** server, user, rule (RoutingRule), dns, geo (GeoList), tls, metrics, audit,
awg_client, ipset_group, routing_direction, direction_sources.

## Жёсткие правила проекта (легко забыть)

- **TS-типы автогенерируются** — не редактировать `frontend/src/api/openapi.ts` руками. Цикл:
  Pydantic-схема → `server.scripts.dump_openapi` → `npm run generate-types`.
  `backend/server/openapi.json` коммитится, CI проверяет drift.
- **`agent.service` в двух местах:** canonical `backend/server/provisioner/agent.service`
  (читается через `importlib.resources` при онбординге) + копия `deploy/agent.service`. Менять
  оба.
- **`SECRET_KEY` обязателен в проде** — нет fallback, fail-fast на старте. В тестах подкидывается
  через `conftest.py`. Для `alembic upgrade head` / `dump_openapi.py` выставлять ENV вручную.
- **mypy `arg-type`/`attr-defined`/`union-attr` отключены для `server.api.*`/`server.tasks.*`** —
  известная боль SQLModel column-descriptors в `where`/`order_by`. Не «чинить».
- **Auth — session-JWT в localStorage** (`waygate-auth`), XSS — открытое окно (план: httpOnly
  cookie + CSRF, в backlog).
- **`/var/lib/waygate-agent/`** — единое writable-место для всех агентских артефактов
  (PrivateTmp/ProtectSystem ломали `/tmp` и `/var/log`).
- **`subprocess_runner.run_command` оборачивает `FileNotFoundError` в `CommandError`**
  (returncode=127) — на slim-системах бинарей может не быть.
- **`agent/Dockerfile` использует `docker-ce-cli`, не `docker.io`** (последний без
  `/usr/bin/docker`).
- **`--privileged` без `Table=off` в `[Interface]`** AmneziaWG hijack'ит default-route → SSH
  обрывается.

## Стиль кода

Python 3.13, **без** `from __future__`. `int | str | None` вместо Optional, `list[int]` вместо
List. StrEnum для всех значимых строк. Все keyword-only аргументы (`def foo(*, x, y)`). Импорт
конечных объектов (`from sqlmodel import Field`). Комментарии и docstring'и **на русском**. ruff
format включён.

## Основные команды

```bash
# Backend: lint + типы + тесты
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q
# Backend integration (нужен docker daemon)
cd backend && uv run pytest -m integration
# Frontend
cd frontend && npm run typecheck && npm run build
# e2e (backend+frontend стартуют автоматически через webServer)
cd frontend && npm run test:e2e
# Регенерация TS-типов после изменения Pydantic-схем
cd backend && uv run python -m server.scripts.dump_openapi > server/openapi.json
cd ../frontend && npm run generate-types
# Prod-стек
cd deploy && docker compose up -d --build   # postgres + server + frontend + nginx
```

## Открытые follow-up'ы (backlog)

ACME-клиент (полный flow), multi-user UI, бекапы, расширение e2e, переход auth на httpOnly
cookie, orphan-cleanup mangle/route внутри чужих netns при удалении container-direction'а,
NFT-8b (UI-config MTU).

---

Дополнительная детализация — в репозитории:
`.claude/SPEC.md` (исходный контракт API), `.claude/ROUTING_ARCHITECTURE.md` (обязательно при
routing-вопросах), `.claude/PROJECT_STATE.md` (полная файловая карта), `.claude/BACKLOG.md`,
`CLAUDE.md`.