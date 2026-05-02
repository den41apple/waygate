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

## Тестирование

- **Backend**: `cd backend && uv run pytest` — **68 passed** (16 агент + 52 сервер; 15 из них — auth-тесты).
- **Frontend**: `cd frontend && npm run typecheck && npm run build`.
- **e2e**: `cd frontend && npm run test:e2e` — **6 passed** (~7-8 сек, реальный backend на sqlite).
- **Compose smoke**: `cd deploy && docker compose up -d --build` — все 4 контейнера healthy за ~12 сек.
- **CI**: `.github/workflows/ci.yml` гоняет всё это (backend → frontend → e2e) на каждый PR.

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
│   ├── models/                  # 6 SQLModel-моделей
│   ├── api/                     # servers, rules, dns, geoip, metrics, tls, provision
│   ├── agent_client/            # aiohttp + tenacity-retry
│   ├── ws/                      # events, auth (JWT), manager, router
│   ├── provisioner/             # ssh, steps, registry, service
│   ├── tasks/metrics_poller.py  # фоновый опрос
│   └── tests/                   # 30 тестов
└── shared/
    ├── pyproject.toml           # waygate-shared
    └── schemas.py               # Pydantic-контракт API агента

frontend/
├── package.json + vite.config.ts + tsconfig.json + index.html
├── Dockerfile + nginx.conf      # multi-stage build → nginx
└── src/
    ├── main.tsx + App.tsx
    ├── styles.css                # CSS-тема панели
    ├── components/               # Icon, primitives, Sidebar, Topbar, Tabs, StatusBar
    ├── pages/                    # 5 табов
    ├── modals/                   # AddServerModal (SSE), TlsModal
    ├── api/                      # типы + fetch + хуки
    ├── ws/                       # useWS, store
    └── store/ui.ts               # Zustand

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
