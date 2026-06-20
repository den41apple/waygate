# 09 — Деплой и ops

Donор: `deploy/{docker-compose.yml,nginx.conf,agent.service}`, `.github/workflows/release-*.yml`.
Для нового проекта (фокус на деплое) — читать вместе с `05`.

## Docker-compose (control-plane стек)

**в доноре: `deploy/docker-compose.yml`**

- 4 сервиса: **postgres** (alpine, volume `pgdata`, healthcheck `pg_isready`) → **server** (FastAPI:8000,
  `depends_on: postgres condition: service_healthy`) → **frontend** (Vite-build → статика) → **nginx**
  (edge, 80/443).
- **Порядок — через `depends_on` + healthcheck**, а не init-скриптами.
- Секреты — в `.env` (`POSTGRES_PASSWORD`, `SECRET_KEY`, `WAYGATE_HOST`, `AGENT_WHEEL_URL`), не в compose.
- Server в entrypoint делает `alembic upgrade head` (нужен `SECRET_KEY` в ENV).
- Smoke: `docker compose up -d --build` — все healthy за ~12с.

## nginx-edge

**в доноре: `deploy/nginx.conf`**

Маршрутизация на границе:
- `/api/` → server. `/ws/` → server **с upgrade-заголовками** (`Upgrade`, `Connection` через
  `map $http_upgrade $connection_upgrade`), длинные таймауты (24ч).
- **SSE-эндпоинты** (provision-stream) → `proxy_buffering off` + длинный read-timeout (иначе буфер
  копит и live-лог не идёт).
- `/` → frontend (статика).
- **Per-endpoint rate-limit зоны** (`limit_req_zone`), не блансет: `auth/ws-token` ~10/мин,
  `servers/provision` ~5/мин (+120с timeout, тяжёлая операция), общий `/api/` ~60/с.
- **Security-заголовки:** CSP `default-src 'self'`, `X-Frame-Options DENY`, `X-Content-Type-Options
  nosniff`, `Referrer-Policy no-referrer`.

> TLS-by-default (80→301→443 + HSTS, certbot-companion) — в доноре оставлен закомментированным
> (backlog). В новом проекте — включить с самого начала.

## systemd-hardening агента

**в доноре: `deploy/agent.service` (canonical — `backend/server/provisioner/agent.service`)**

- `ProtectSystem=strict` — `/usr`, `/etc`, `/var` read-only, кроме явных путей.
- **`ReadWritePaths=/var/lib/<agent> /etc/<agent> /etc/dnsmasq.d`** — и **почему это важно**:
  - `/var/lib/<agent>` — единственное надёжное writable-место для self-update-артефактов,
    `last-apply.json`, логов. `/tmp` убивает `PrivateTmp=true`; `/var/log` режет `ProtectSystem`.
  - доменные пути (`/etc/dnsmasq.d`) — куда агент пишет конфиги.
- `ProtectHome=true`, `PrivateTmp=true`. Агент рутовый (нужен `CAP_NET_ADMIN`); при желании можно
  сузить до capabilities-only.
- `Restart=always`, `RestartSec=5`.

**Reuse-урок:** sandbox systemd'а предотвращает боковое движение, но **каждый writable-путь требует
явного `ReadWritePaths` — и его отсутствие ломает self-update «молча»** (см. `04`, `11`).

## Релизные пайплайны (tag-per-component)

**в доноре: `.github/workflows/release-{agent,server,awg-client}.yml`**

- **Свой namespace тегов на компонент:** `agent-v*`, `server-v*`, `awg-client-v*` — чтобы релизы не
  пересекались.
- `release-agent`: тег `agent-v*` → `uv build --wheel` → GitHub Release + asset
  `waygate_agent-py3-none-any.whl`. **Стабильный URL `/latest/download/...`** — его агент использует
  в self-update (см. `04`). Control-plane знает его через `AGENT_WHEEL_URL`.
- `release-server`: тег `server-v*` → multi-arch (`linux/amd64,linux/arm64` через buildx) → GHCR
  `latest` + `{version}`.

## Чеклист «новый хост в проде»

1. Открыть нужные порты (для VPN — UDP-range; важные TCP-сервисы — точечно).
2. Онбординг по SSH (см. `05`) — он сам поставит зависимости + `iptables-nft` alternative.
3. После apply — sanity-check, что правила реально в ядре (`nft list ...`), а не в shadow-view.
4. Self-update до нужной версии через UI/control-plane (пути self-update'а — `/var/lib/...`).
