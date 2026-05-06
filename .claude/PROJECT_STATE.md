# Waygate — состояние проекта

Краткая выжимка для будущих сессий: что есть, на чём остановились, какие решения
приняты по дороге.

---

## Что это

Веб-панель управления GeoIP-маршрутизацией трафика на парке Linux-серверов.
Каждый сервер маршрутизирует трафик через AmneziaWG-туннели по стране назначения,
домену или ipset-правилам. Панель управляет всеми из одной точки.

## Компоненты

- **backend/agent** — `waygate-agent`, FastAPI-демон на каждом managed server.
  Идемпотентный apply на ipset/iptables/ip rule, GeoIP atomic swap, scheduler через
  APScheduler 4, self-update через wheel + systemctl. SSH-онбординг разворачивает
  его автоматически.
- **backend/server** — `waygate-server`, control-plane FastAPI + SQLModel +
  alembic. CRUD ресурсов (servers/rules/dns/geoip/tls/metrics), agent_client на
  aiohttp + tenacity, фоновый metrics-poller, WebSocket /ws/events, SSE для
  онбординга, JWT для WS.
- **backend/shared** — `waygate-shared`, Pydantic-схемы API агента (контракт
  agent ↔ server, дословно по [SPEC](SPEC.md)).
- **frontend** — Vite + React 18 + TypeScript + TanStack Query + Zustand.
  Дизайн-прототип перенесён в модульные TSX-компоненты, WS-хук с
  auto-reconnect и invalidateQueries по событиям, SSE для онбординга в
  AddServerModal.

## Статус (по фазам SPEC)

| # | Фаза | Done |
|---|---|---|
| 1 | Фундамент: uv workspace, ruff/mypy, schemas | ✅ |
| 2 | Агент MVP: idempotent routing/geoip/tunnels + auth | ✅ |
| 3 | Сервер MVP: модели, alembic, agent_client, REST, poller | ✅ |
| 4 | Онбординг: asyncssh + SSE | ✅ |
| 5 | WebSocket + JWT + emit из всех роутеров | ✅ |
| 6 | TLS upload+path + APScheduler + self-update | ✅ |
| 7 | Frontend: Vite + TanStack + WS + SSE | ✅ |
| 8 | Деплой: docker-compose + nginx + systemd + README | ✅ |
| — | CI/CD: GitHub Actions ruff/mypy/pytest + agent-wheel + server-image | ✅ |
| — | Закрытие техдолга: dns/apply, token/rotate, healthcheck, prometheus, audit, security headers, rate limit, regex-validators, SYSTEMD drift, tweaks, tunnels-фронт, openapi-autogen | ✅ |
| — | **Auth (Variant B)**: User+bcrypt+JWT, login/me/logout, защита роутеров, bootstrap-админ из ENV, кнопка «Выход» в Topbar | ✅ |
| — | **e2e Playwright**: 6 тестов (auth/onboarding-error+retry/server-crud-via-rest), CI job | ✅ |
| — | **AddServerModal UX-фикс**: не уходим на «Готово» при ошибке, кнопки Повторить/Назад | ✅ |
| — | **Routing-directions редизайн**: новая модель `RoutingDirection` (header) → N child-`RoutingRule`'ов с общим fwmark/table_id; UI с multi-select (geo/dns/ipset одной плашкой через одну fwmark), 4 главных таба (Routing/Tunnels/Lists/Metrics), Tunnels с под-табами Клиенты/Серверные, Lists с под-табами GeoIP/DNS/IPset, Custom IPset как третья сущность; data-migration legacy `RoutingRule` встроена в alembic-ревизию `e92c5b1f3a87` (применяется автоматически) | ✅ |
| — | **Self-update + edit-формы + integration-тесты**: agent `0.2.0` с фикс update-paths (writable `/var/lib/waygate-agent/`); `GET /api/v1/agent-releases` GitHub-прокси + UI dropdown с list версий; PATCH-endpoints для GeoList/Server, расширение IpsetGroupUpdate; edit-режим в 4 модалках + EditServerModal; mypy `check_untyped_defs=true` для тестов; `UV_PROJECT_ENVIRONMENT=/usr/local` в Dockerfile'ах; +4 e2e (directions/dns/server-edit/update-agent); +3 integration с реальным Docker-контейнером | ✅ |
| — | **Backlog batch 2026-05-05**: WS-events для ipset_groups (#12), agent updater paths (#13), DNS-prereq workaround drop (#14/15), reconcile AwgClient.status (#16), interface_name в /v1/clients (#20), v6-skip per-rule (#21a), catch-all direction `is_default_egress` + миграция `f3a91d4c2e8b` (#0b), edit-формы по всем сущностям (#11) | ✅ |
| — | **Аудит-batch 2026-05-05 (A1-A5/B1-B5/C1-C6)**: SECRET_KEY fail-fast, parallel metrics-poller через asyncio.gather, Server.host index, pagination, `direction_sources` pivot table вместо string-magic в _collect_refs, WS event-types codegen (Python enum → TS literals), AgentClient ParamSpec'нутый retry-decorator, hoist queries из DirectionCard, useModalsStore. +6 новых тест-сьютов (config, metrics_poller, audit_redaction, lifespan, scheduler, ssh_integration) | ✅ |
| — | **Test-AWG-server 2026-05-06**: `scripts/setup-test-awg-server.sh` обновлён на полные AWG 2.0 параметры из `AWG_PARAMS.txt` (S3/S4, I2-I5, H-ranges); параметры вынесены в переменные для гарантии симметрии server/client. **Корень проблемы из SESSION_2026_05_04 найден**: на Ubuntu 24.04 + Docker 28+ команды `iptables` попадают в shadow-chain от iptables-nft-compat и НЕ срабатывают (policy=drop в реальной Docker-managed `chain ip filter FORWARD`). Решение — писать через `nft` напрямую. Зафиксировано в `ROUTING_ARCHITECTURE.md` + `SESSION_2026_05_06.md` + memory. **Open follow-up:** проверить `agent/routing.py` на ту же проблему (BACKLOG NFT-1), переписать скрипт на `nft` (BACKLOG NFT-2). | ✅ (диагноз) / ⏳ (рефакторинг) |

## Тестирование

- **Backend (unit/api)**: `cd backend && uv run pytest` — **201 passed**.
- **Backend (integration)**: `cd backend && uv run pytest -m integration` — **13 passed** (~50-60 сек, поднимает реальный `--privileged` agent-контейнер с ipset/iptables/dnsmasq + Ubuntu sshd-контейнер; гоняет HTTP к живому granian'у и SSH-flow против реального OpenSSH'а). По умолчанию выключены через `addopts`. Запускать локально с docker daemon (или в CI на release-tags).
- **Frontend**: `cd frontend && npm run typecheck && npm run build`.
- **e2e**: `cd frontend && npm run test:e2e` — **11 passed** (~20 сек, реальный backend на sqlite). Покрывают auth, server CRUD, AWG-client add, onboarding, **directions**, **DNS**, **server-edit**, **update-agent** (с моком GitHub-прокси).
- **Compose smoke**: `cd deploy && docker compose up -d --build` — все 4 контейнера healthy за ~12 сек.
- **CI**: `.github/workflows/ci.yml` гоняет всё это (backend → frontend → e2e) на каждый PR. Integration-тесты добавить отдельным job'ом для release-tags (BACKLOG #18).

## Ключевые решения, принятые по дороге

(Подробности в логе обсуждений — здесь только итог.)

### Структура / тулинг
- **Flat layout** в backend (`backend/agent/main.py` без вложенного `waygate_agent/`),
  через `setuptools.package-dir = {"agent" = "."}`. Granian-команда — `agent.main:app`,
  не `waygate_agent.main:app` (отклонение от SPEC).
- **Один uv workspace** в `backend/`, корневого `pyproject.toml` нет. Frontend — отдельно.
- **Build-backend setuptools, не hatchling** — для flat-layout с переименованием пакета.
- **mypy и pytest** конфиги — в `backend/pyproject.toml` (нет mypy.ini).
- `ruff format` включён, vertical alignment из SPEC не сохраняем (см.
  [memory/spec_is_indicative.md](memory/spec_is_indicative.md)).

### Агент
- **APScheduler 4.0a** с `AsyncScheduler`. В тестах не входим в lifespan (TaskGroup
  не дружит с pytest-asyncio fixture-ами) — `AgentState` инициализируем руками.
- **Idempotent apply_rules**: читаем iptables mangle PREROUTING, ip rule, ip route
  table, удаляем orphan'ы, добавляем недостающее. `ip route replace` — атомарно.
- **GeoIP atomic swap** — `ipset restore` во временный set + `ipset swap` без
  обрыва трафика.
- **TLS три режима**: `upload` (base64 cert/key + SIGUSR1), `path` (симлинки на
  существующие файлы), `acme` — **скелет, не реализовано**. Полный ACME-flow
  (account/JOSE/HTTP-01/DNS-01) откладывается.
- **Self-update**: download wheel → `pip install --upgrade` → отложенный
  `systemctl restart` фоновой таской с сохранением reference (RUF006).

### Сервер
- **Globals для engine** заменены на `lru_cache` на `get_engine`/`get_session_maker`.
  В тестах используется `app.dependency_overrides[get_session]`.
- **mypy overrides** для `server.api.*` и `server.tasks.*` — `disable_error_code = arg-type`
  из-за известной проблемы SQLModel-column-descriptors в `where`/`order_by`.
- **`AgentUnreachable` без суффикса Error** — N818 в ignore (читается лучше).
- **WS broadcast в singleton** `ConnectionManager.get_manager()` — доступен из
  background-тасок без `app.state`.

### Frontend
- **TypeScript strict**, ручные типы в `src/api/types.ts`. Скрипт
  `npm run generate-types` через `openapi-typescript` есть, но не запускается на
  CI пока (нужен поднятый backend).
- **WS hook** инвалидирует `queryKey` под каждое `EventType` через `queryClient`.
- **SSE через EventSource** в `AddServerModal.tsx` — реальный live-лог онбординга.
- **TweaksPanel** из дизайна не переносим (Claude Design dev-utility).
- **persist v2 в `store/ui.ts`** — `migrate` хук в `zustand/middleware/persist`
  мапит старые `activeTab=geoip|dns` в `lists` (после редизайна табов в Sprint 3).

### Routing-directions архитектура (Sprint 1-4)
- **`RoutingDirection` (header) + N child-`RoutingRule`'ов** с общим
  `fwmark`/`table_id`/`via_*`. Direction = «трафик из {GeoIP-зон, DNS-правил,
  IPset-групп} через VPN-клиента X». Поле `RoutingRule.direction_id` с
  `ondelete=CASCADE` через `sa_column` — нужно чтобы CASCADE работал и в alembic,
  и в SQLite-тестах через `metadata.create_all`. `_materialize_rules()` создаёт
  по одному child-RoutingRule на каждый ref с одной общей fwmark.
- **Reverse-lookup `_collect_refs`** — из child-правил восстанавливаем какие
  `geo_list_ids/dns_rule_ids/ipset_group_ids` участвовали (по `ipset_name`).
  Используется в GET-ответе и в `update_direction` без diff'а.
- **`IpsetGroup` как третья сущность** — Custom CIDR-списки без GeoIP/DNS;
  агентский `apply_custom_ipset()` с atomic-swap.
- **Data-migration legacy → directions** встроена в alembic-ревизию
  `e92c5b1f3a87` через `op.get_bind()` + сырой SQL. Группирует по
  `(server_id, via_interface, via_gateway, fwmark, table_id, scope, scope_target)`;
  имена `legacy-<iface>` с автоинкрементом при коллизии. Применяется
  автоматически на `alembic upgrade head`.

### Self-update + integration-тесты (Sprint 5)
- **`/var/lib/waygate-agent/`** — единое writable-место для всех агентских
  артефактов (`update-swap.sh`, `update.log`, ранее в `/tmp` гибли с
  `PrivateTmp=true`, в `/var/log` падали на `ProtectSystem=strict`).
- **`GET /api/v1/agent-releases`** — server-side прокси к GitHub Releases с
  5-мин кешем (защита от unauthenticated rate-limit 60/час). Фильтр по тегам
  `agent-v*`, возвращает `[{tag, version, name, published_at, wheel_url}]`.
  Frontend `useAgentReleases()` → select в `UpdateAgentModal` с default=latest.
- **Edit-формы** — реюз `AddXxxModal` через prop `editing?: T`. PATCH-endpoint
  для GeoList и Server (новые), `IpsetGroupUpdate` расширен на name. Pencil-icon
  в карточках, `EditServerModal` отдельный компонент. Server PATCH меняет только
  `name`/`region` (host/port/token = переонбординг или token/rotate).
- **Integration-тесты** — `agent/tests/test_integration.py`, marker
  `@pytest.mark.integration`. Поднимает `--privileged`-контейнер с агентом,
  mount `/var/run/docker.sock` (агенту нужен docker CLI для `docker ps`).
  Build session-scope, контейнер module-scope, HTTP-probe (TCP мало).
- **`subprocess_runner.run_command` ловит `FileNotFoundError`** — оборачивает
  в `CommandError(returncode=127)`. Без этого `systemctl reload dnsmasq` на
  системе без systemd падал в 500 мимо `except CommandError` в agent/dns.py.

### Аудит-batch (Sprint 6, 2026-05-05)
- **`SECRET_KEY` обязателен в проде** — `server/config.py::_require_secret_key()`
  бросает RuntimeError на module-load если не задан или совпадает с известным
  dev-default. `conftest.py` подкидывает test-value до import'ов; для alembic
  и `dump_openapi.py` — выставлять руками.
- **`metrics_poller` параллельный** — `_fetch_metrics` в `asyncio.gather`,
  persist+broadcast sequentially. Один зависший агент больше не пинит весь цикл.
- **`Server.host` индекс** + миграция `c8d2a4f1e9b3` — provision делает upsert
  по host'у, full-scan на тысячах серверов был бы заметным.
- **`direction_sources` pivot** + миграция `d5e9a3c4b1a8` — заменил
  string-matching reverse-lookup в `_collect_refs` на типизированную таблицу
  `(direction_id, source_type, source_id)`. Добавление нового source-type =
  одно enum-значение в `DirectionSourceType` + handler в materialize. Старая
  логика парсинга `geoip-ru-v4` → GeoList(country=ru) полностью удалена.
- **WS event-types codegen** — `server/scripts/dump_ws_events.py` экспортит
  `EventType` enum в `frontend/src/api/wsEventTypes.gen.ts`. Запускать после
  правок `EventType`. CI drift-check желательно добавить (как для openapi.json).
- **`AgentClient` ParamSpec'ный retry-decorator** — типы декорируемых методов
  сохраняются. Helpers `_typed_get`/`_typed_post` для не-retry-методов.
- **`useModalsStore`** — единый Zustand-store для `addServer/tls/updateAgent/
  editServer`. Старый `showAddServer/showTls/showUpdate` в `useUiStore` удалён.
- **`AwgClientInfo.interface_name` Optional** — back-compat со старыми
  агентами, у которых поле ещё не сериализуется (control-plane обновляется
  быстрее парка). Server-side fallback через `iface_name_for(name=client.name)`
  из DB-имени (формула в `shared/awg_naming.py`).

## Известные TODO

Все недоделки и follow-up'ы — в [BACKLOG.md](BACKLOG.md), там 4 раздела:

1. **Реальные пробелы по SPEC** — `/v1/dns/apply` на агенте, `/v1/token/rotate`,
   отдельная healthcheck-таска.
2. **Сознательные стабы** — ACME-клиент, renewal-trigger, первый wheel-release,
   tweaks-функционал во фронте.
3. **Технический долг** — drift `SYSTEMD_UNIT_TEMPLATE`, openapi-typescript в CI,
   301-редирект, валидация ipset-имён, богатый `/v1/tunnels` не дотянут до фронта.
4. **Production-readiness** — auth, audit log, backups, Prometheus, TLS-by-default,
   rate limiting, e2e тесты, хранение SSH-кредов.

## Файловая карта

```
backend/
├── pyproject.toml               # uv workspace + ruff + mypy + pytest
├── agent/
│   ├── pyproject.toml           # waygate-agent
│   ├── Dockerfile               # dev-образ агента
│   ├── main.py                  # FastAPI + lifespan + 7 эндпоинтов
│   ├── auth.py                  # Bearer-dependency
│   ├── config.py                # Settings через envparse
│   ├── routing.py               # idempotent apply_rules
│   ├── geoip.py                 # atomic ipset replace
│   ├── tunnels.py               # docker ps + wg show
│   ├── metrics.py               # ring buffer
│   ├── tls.py                   # upload/path (acme — скелет)
│   ├── updater.py               # self-update flow
│   ├── scheduler.py             # APScheduler 4
│   ├── subprocess_runner.py     # asyncio.create_subprocess_exec обёртка
│   └── tests/                   # 14 тестов
├── server/
│   ├── pyproject.toml           # waygate-server
│   ├── Dockerfile               # control-plane образ
│   ├── alembic.ini + alembic/   # миграции
│   ├── main.py                  # FastAPI + CORS + lifespan + роутеры + WS
│   ├── config.py + db.py        # Settings + async engine
│   ├── models/                  # SQLModel: server, user, rule, dns, geo, tls, metrics,
│   │                            #          audit, awg_client, ipset_group, routing_direction
│   ├── api/                     # servers, rules, directions, ipset_groups, clients, dns,
│   │                            #          geoip, metrics, tls, provision, audit, auth,
│   │                            #          agent_releases (GitHub-прокси)
│   ├── agent_client/            # aiohttp + tenacity-retry
│   ├── ws/                      # events, auth (JWT), manager, router
│   ├── provisioner/             # ssh, steps, registry, service
│   ├── scripts/dump_openapi.py  # генерация openapi.json для CI drift-check
│   ├── tasks/metrics_poller.py  # фоновый опрос + healthcheck
│   └── tests/                   # ~85 тестов
└── shared/
    ├── pyproject.toml           # waygate-shared
    ├── schemas.py               # Pydantic-контракт API агента
    ├── awg_config.py            # парсер AmneziaWG `.conf`
    └── awg_naming.py            # iface_name_for / container_name_for —
                                 # одна формула для agent/server/frontend

frontend/
├── package.json + vite.config.ts + tsconfig.json + index.html
├── Dockerfile + nginx.conf      # multi-stage build → nginx
└── src/
    ├── main.tsx + App.tsx
    ├── styles.css                # CSS-тема панели
    ├── components/               # Icon, primitives, Sidebar, Topbar, Tabs, StatusBar,
    │                             # CountrySelect (datalist + flag)
    ├── pages/                    # 4 главных таба: RoutingTab, TunnelsTab (sub-tabs
    │                             # Клиенты/Серверные), ListsTab (sub-tabs GeoIP/DNS/IPset),
    │                             # MetricsTab. Внутренние страницы под Lists:
    │                             # GeoIpTab, DnsTab, IpsetGroupsTab.
    ├── modals/                   # AddServerModal (SSE), EditServerModal, TlsModal,
    │                             # UpdateAgentModal (с select версий из
    │                             # /api/v1/agent-releases),
    │                             # AddRoutingDirectionModal (multi-select),
    │                             # AddDnsModal, AddGeoListModal, AddCustomIpsetModal,
    │                             # AddAwgClientModal, QrModal.
    │                             # Все Add*Modal принимают `editing?: T` —
    │                             # pre-fill + PATCH вместо POST.
    ├── api/                      # типы + fetch + хуки (servers, directions, rules,
    │                             # dns, geoip, ipsetGroups, awgClients, ...)
    ├── ws/                       # useWS — invalidate по EventType (включая direction.*)
    └── store/ui.ts               # Zustand persist v2 (TabId migration geoip|dns → lists)

deploy/
├── docker-compose.yml            # postgres + server + frontend + nginx
├── nginx.conf                    # edge: /api → server, /ws → server, / → frontend
├── agent.service                 # systemd unit (синхронен с inlined в steps.py)
├── renew-hook.sh                 # SIGUSR1 для granian
└── .env.example                  # POSTGRES_PASSWORD, SECRET_KEY, WAYGATE_HOST

.github/workflows/
├── ci.yml                        # backend (ruff/mypy/pytest) + frontend (typecheck/build)
├── release-agent.yml             # тег agent-v* → wheel в GitHub Release
└── release-server.yml            # тег server-v* → multi-arch image в GHCR

.claude/                          # эта папка
├── SPEC.md                       # полная спецификация (источник истины)
├── STYLE.md                      # стиль кода
├── PROJECT_STATE.md              # вы здесь
└── memory/                       # заметки для будущих сессий
```

## Запуск

**Dev:**
```bash
# Backend
cd backend
uv sync --all-packages
cd server && uv run alembic upgrade head && cd ..
uv run granian --interface asgi --http 1 server.main:app --host 0.0.0.0 --port 8000

# Frontend (соседний терминал)
cd frontend
npm install
npm run dev   # http://localhost:5173
```

**Production:**
```bash
cd deploy/
cp .env.example .env  # отредактировать пароли
docker compose up -d --build
```
