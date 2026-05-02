# Waygate

Веб-панель управления GeoIP-маршрутизацией трафика на нескольких Linux-серверах.
Каждый сервер маршрутизирует трафик через AmneziaWG-туннели по стране назначения,
домену или ipset-правилам. Панель управляет всем парком серверов из одной точки.

## Архитектура

Два сервиса:

- **server** (control plane) — единый бэкенд с REST API и WebSocket, FastAPI на granian.
  Хранит состояние в PostgreSQL/SQLite, общается с агентами через HTTP.
  Прокидывает Prometheus `/metrics`, пишет audit-log на каждую mutation.
- **agent** — лёгкий демон на каждом управляемом сервере. Управляет ipset, iptables,
  dnsmasq, AmneziaWG-контейнерами и собственным TLS-сертификатом. Не имеет БД,
  всё состояние применяется командами от control-plane. Тоже отдаёт `/metrics`.

Связь:

- **panel ↔ agent**: HTTP по публичному IP агента (порт 7743 по умолчанию), Bearer-токен.
- **frontend ↔ panel**: REST для CRUD, WebSocket `/ws/events` для live-обновлений,
  SSE `/api/v1/servers/{id}/provision/stream` для лога онбординга.

## Структура репо

```
backend/
├── pyproject.toml          # uv workspace
├── agent/                  # waygate-agent (FastAPI + APScheduler + acme/cryptography)
├── server/                 # waygate-server (FastAPI + SQLModel + Alembic + asyncssh)
└── shared/                 # waygate-shared (Pydantic-схемы API агента)

frontend/                   # Vite + React 18 + TypeScript + TanStack Query + Zustand

deploy/
├── docker-compose.yml      # postgres + server + frontend + nginx
├── nginx.conf              # security headers + rate limiting + reverse proxy
├── agent.service           # systemd unit для агента (копия canonical из backend/server/provisioner/)
└── renew-hook.sh           # SIGUSR1 для granian после обновления сертификата

.claude/                    # внутренняя документация
├── SPEC.md                 # полная спецификация
├── STYLE.md                # стиль кода
├── PROJECT_STATE.md        # текущее состояние и решения
└── BACKLOG.md              # follow-up'ы
```

## Quick start (Docker Compose)

```bash
cd deploy/
cp .env.example .env
# отредактируйте: POSTGRES_PASSWORD, SECRET_KEY, WAYGATE_ADMIN_USER/PASSWORD
docker compose up -d --build
```

Открыть `http://localhost`. Появится форма входа — логинитесь под admin-кредами
из `.env` (`WAYGATE_ADMIN_USER`/`WAYGATE_ADMIN_PASSWORD`).

После логина — пустой список серверов. Добавьте первый через кнопку «Добавить
сервер» в левом нижнем углу. Понадобится SSH-доступ к Linux-серверу с Docker и
AmneziaWG-контейнером.

### Создание первого администратора

При старте `server` контейнера читается ENV. Если в БД нет пользователя с
указанным `WAYGATE_ADMIN_USER`, он создаётся (`is_admin=True`). Если
`WAYGATE_ADMIN_USER` или `WAYGATE_ADMIN_PASSWORD` пустые и в БД нет ни одного
юзера — в логе будет warning и войти будет невозможно. Просто заполните env и
перезапустите `server`-сервис.

Дополнительные пользователи пока создаются через прямой SQL или REST (см.
`/api/v1/auth/...` в OpenAPI). UI для управления юзерами — в backlog.

### TLS на edge

По умолчанию nginx слушает только 80. Для HTTPS:

1. Положите `cert.pem` и `key.pem` в `deploy/tls/`.
2. Раскомментируйте 443-блок в `deploy/nginx.conf`.
3. `docker compose restart nginx`.

### Безопасность edge nginx

В `deploy/nginx.conf` уже включены:

- **Security headers**: `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- **Rate limiting**: 5 req/min на `POST /api/v1/servers/provision`,
  10 req/min на `POST /api/v1/auth/ws-token`, 60 req/sec на остальной `/api/`.

При включении TLS добавьте HSTS — раскомментированный 443-блок в шаблоне это
уже учитывает.

## Dev-режим (без Docker)

Понадобятся Python 3.13+, [uv](https://github.com/astral-sh/uv), Node.js 20+.

### Backend

```bash
cd backend
uv sync --all-packages

# Применить миграции (по умолчанию SQLite-файл backend/server/waygate.db)
cd server && uv run alembic upgrade head && cd ..

# Запустить control-plane
uv run granian --interface asgi --http 1 server.main:app --host 0.0.0.0 --port 8000
```

В соседнем терминале — frontend:

```bash
cd frontend
npm install
npm run dev
```

Vite поднимется на `http://localhost:5173` и сам проксирует `/api` и `/ws` на :8000.

### Тесты и проверки

```bash
cd backend
uv run pytest -v          # 68 тестов (16 агент + 52 сервер, в т.ч. auth)
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

```bash
cd frontend
npm run typecheck
npm run build
```

### e2e (Playwright)

```bash
cd frontend
npx playwright install chromium  # один раз — кеширует браузер
npm run test:e2e                  # 6 тестов, ~7-8 сек
npm run test:e2e:ui               # интерактивный режим для отладки
```

Playwright сам поднимает backend (на временной sqlite БД с админом
`admin/admin-e2e-pass-123`) и frontend (vite dev) в `webServer`-блоке
`playwright.config.ts`.

### Регенерация TS-типов после изменения Pydantic-схем

```bash
cd backend && uv run python -m server.scripts.dump_openapi > server/openapi.json
cd ../frontend && npm run generate-types
git add backend/server/openapi.json frontend/src/api/openapi.ts
```

CI проверяет drift и упадёт, если коммитнуть Python-схему без обновлённых
TS-типов.

## Добавление сервера (онбординг)

Через UI — кнопка «Добавить сервер» открывает 3-шаговый wizard. На шаге 0
заполняете SSH-доступ, на шаге 1 видите live-лог установки агента (через SSE).

Что делает онбординг под капотом:

1. SSH-подключение к серверу под указанным юзером с password или private key.
2. Проверка ОС (Ubuntu/Debian).
3. `apt-get install ipset iptables iproute2 dnsmasq curl openssl python3-venv wireguard-tools`.
4. Поиск AmneziaWG-контейнеров (`docker ps | grep amnezia`).
5. Самоподписанный TLS-сертификат через `openssl req -x509`.
6. Скачивание wheel'а агента из `AGENT_WHEEL_URL` → `/tmp/waygate-agent.whl`.
7. `python3 -m venv /opt/waygate-agent` → `pip install ...`.
8. Запись `/etc/waygate/agent.env` (токен, порты).
9. Установка systemd unit'а (`backend/server/provisioner/agent.service`).
10. `systemctl daemon-reload && enable --now waygate-agent`.
11. Healthcheck — control-plane дёргает `/v1/status` пока не получит 200.

После провижионинга агент онлайн, в БД появляется запись с токеном, через
WS-канал прилетает `server.status_changed`, и фронт сразу его подсвечивает.
В audit-log появляются записи о POST'ах.

> SSH-креды используются один раз и не сохраняются ни в БД, ни на диск (минимум
> attack surface). Если потеряли доступ к агенту — придётся переустановить или
> вручную обновить токен.

### Wheel агента

Перед провижионингом нужно собрать wheel и положить его на доступный URL —
обычно GitHub Releases. Локально:

```bash
cd backend/agent
uv build --wheel
# wheel появится в backend/agent/dist/
```

Загрузите его в Release и пропишите URL в `AGENT_WHEEL_URL` (env var
control-plane или `.env` для compose).

В CI workflow `release-agent.yml` собирает и публикует wheel автоматически на
тег `agent-v*`.

## Управление агентом

### TLS на агенте

Три режима в модалке «TLS» в Topbar:

- **upload** — заливаете cert/key прямо через API. Granian перечитывает через SIGUSR1.
- **path** — указываете путь к существующим файлам (например, `/etc/letsencrypt/live/...`).
  При renewal certbot вызывает `deploy/renew-hook.sh`, который шлёт SIGUSR1.
- **acme** — встроенный ACME-клиент (Let's Encrypt, HTTP-01 / DNS-01). Пока в
  разработке (см. `.claude/BACKLOG.md`).

### Ротация Bearer-токена

Кнопка «Токен» в Topbar или REST:

```bash
curl -X POST http://localhost/api/v1/servers/1/token/rotate
```

Агент сгенерирует новый `secrets.token_urlsafe(48)`, перепишет
`/etc/waygate/agent.env` и обновит `settings.token` в памяти. Старый токен
становится невалидным **немедленно**.

### Самообновление

Через UI кнопка «Update» на топбаре, либо API:

```bash
curl -X POST http://localhost/api/v1/servers/1/update \
  -H 'Content-Type: application/json' \
  -d '{"version":"0.2.0","wheel_url":"https://.../waygate_agent-0.2.0.whl"}'
```

Агент скачивает wheel, ставит его в свой venv, отвечает
`200 {status:"restarting"}`, потом `systemctl restart waygate-agent`.
Control-plane дальше polling'ит `/v1/status` пока новая версия не появится и
эмиттит `SERVER_AGENT_UPDATED` через WS.

## Audit log

Любая mutation (POST/PATCH/DELETE/PUT) на `/api/v1/*` пишется в таблицу
`audit_entries` с redact'нутым телом. Sensitive-ключи (`password`, `cert_pem`,
`key_pem`, `dns_api_key`, `token`) заменяются на `***`.

```bash
# Последние 24 часа
curl http://localhost/api/v1/audit?range=24h

# Только конкретный сервер за неделю
curl 'http://localhost/api/v1/audit?range=7d&server_id=1'
```

## Метрики (Prometheus)

И server, и agent отдают `/metrics` в формате Prometheus
(`prometheus-fastapi-instrumentator`):

- request rate / latency / in-progress
- автоматические http-метрики FastAPI

На сервере `/metrics` доступен только внутри docker network (nginx не проксирует).
Скраппер должен ходить напрямую на server:8000.

На агенте `/metrics` открыт без auth — в production ограничьте доступ через
iptables-правило (только с IP control-plane).

## Конфигурация

### server

| ENV                             | Default                                | Что делает                          |
|---------------------------------|----------------------------------------|-------------------------------------|
| `DATABASE_URL`                  | `sqlite+aiosqlite:///waygate.db`       | URL базы — sqlite или postgresql    |
| `SECRET_KEY`                    | dev-default                            | Подпись session/WS-JWT              |
| `WAYGATE_ADMIN_USER`            | (пусто)                                | Bootstrap первого админа при старте |
| `WAYGATE_ADMIN_PASSWORD`        | (пусто)                                | См. выше                            |
| `SESSION_TTL_SECONDS`           | `43200` (12 часов)                     | Срок жизни session-JWT              |
| `PORT`                          | `8000`                                 | Порт control-plane                  |
| `CORS_ORIGINS`                  | `http://localhost:5173`                | Разрешённые origin'ы                |
| `METRICS_POLL_SECONDS`          | `30`                                   | Интервал сбора rx/tx с агентов      |
| `HEALTHCHECK_SECONDS`           | `60`                                   | Интервал лёгкого пинга агентов      |
| `METRICS_RETENTION_DAYS`        | `30`                                   | Сколько хранить точки rx/tx         |
| `AGENT_WHEEL_URL`               | GitHub Releases                        | Откуда качать wheel при провижионинге |
| `AGENT_DEFAULT_PORT`            | `7743`                                 | Дефолтный порт агента               |
| `PROVISION_HEALTHCHECK_TIMEOUT` | `120`                                  | Сколько ждать reconnect'а агента    |

### agent

| ENV               | Default                       | Что делает                |
|-------------------|-------------------------------|---------------------------|
| `PORT`            | `7743`                        | На каком порту слушает    |
| `TOKEN`           | (обязательно)                 | Bearer-токен              |
| `LOG_LEVEL`       | `INFO`                        |                           |
| `TLS_DIR`         | `/etc/waygate/tls`            | Директория с cert.pem     |
| `DATA_DIR`        | `/var/lib/waygate-agent`      | Состояние                 |
| `METRICS_INTERVAL_SECONDS` | `30`                  | Сбор метрик               |

## API: список эндпоинтов

REST под `/api/v1`:

- **servers**: `GET /servers`, `POST /servers` (manual add), `GET /servers/{id}`,
  `DELETE /servers/{id}`, `POST /servers/{id}/refresh`,
  `POST /servers/{id}/token/rotate`, `POST /servers/{id}/update`.
- **provision**: `POST /servers/provision` (SSH-онбординг),
  `GET /servers/{id}/provision/stream` (SSE).
- **rules**: `GET /servers/{id}/rules`, `POST`, `PATCH /{rid}`, `DELETE /{rid}`,
  `POST /servers/{id}/rules/apply`.
- **dns**: `GET /servers/{id}/dns`, `POST`, `PATCH /{rid}`, `DELETE /{rid}`,
  `POST /servers/{id}/dns/apply`.
- **geoip**: `GET /geoip/lists`, `POST /geoip/lists`,
  `DELETE /geoip/lists/{id}`, `POST /servers/{id}/geoip/sync`.
- **tls**: `GET /servers/{id}/tls`, `POST /servers/{id}/tls`.
- **tunnels**: `GET /servers/{id}/tunnels` (proxy на агентский `/v1/tunnels`).
- **metrics**: `GET /servers/{id}/metrics?range=1h|6h|24h`.
- **audit**: `GET /audit?range=1h|24h|7d&server_id=&limit=`.
- **auth**: `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`, `POST /auth/ws-token`.

WS: `/ws/events?token=<jwt>` — общий канал событий.

Полный OpenAPI всегда доступен на `/openapi.json` и валидно сериализуется через
`uv run python -m server.scripts.dump_openapi`.

## Что осталось сделать

См. [`.claude/BACKLOG.md`](.claude/BACKLOG.md): ACME-клиент, renewal-trigger,
первый GitHub Release wheel'а, auth-система control-plane, бекапы Postgres,
TLS-by-default, e2e Playwright и пара мелких пунктов.

## Лицензия

(Уточняется)
