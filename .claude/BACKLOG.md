# Waygate — backlog недоделанного

Открытые follow-up'ы. Большая часть требует отдельной инфраструктуры или решений
вне scope обычной итерации.

Содержание:

- [Что осталось делать](#что-осталось-делать)
- [Что уже закрыто (для истории)](#что-уже-закрыто-для-истории)

---

## Что осталось делать

### 1. ACME-клиент в `backend/agent/tls.py::_apply_acme`

**Состояние:** функция бросает `TlsApplyError("ACME HTTP-01/DNS-01 пока не реализован")`. Validate в `TlsConfig` пропускает `mode=acme`, дальше — стенка.

**Почему отложено:** требует реального сервера и домена для теста — на dev-машине не воспроизвести.

**HTTP-01 (~150 строк):**
- Account key (Ed25519) → `acme.client.ClientV2.new_account(...)`.
- `new_order(domains)` → `get_authorizations()` → выбрать challenge с `type="http-01"`.
- Поднять временный `aiohttp.web` сервер на :80 с маршрутом `/.well-known/acme-challenge/{token}` → возвращает `key_authorization`.
- `respond_to_challenge(challenge)` → polling order до `valid` → `finalize_order` → скачать cert.
- Записать в `TLS_DIR/cert.pem`, `TLS_DIR/key.pem`, SIGUSR1 на granian.

**DNS-01 (~150 строк):**
- Те же шаги, challenge `type="dns-01"`.
- DNS-провайдеры — отдельный модуль `backend/agent/acme_dns/`:
  - `cloudflare.py` (POST `/zones/{zone_id}/dns_records`),
  - `desec.py` (PUT `/domains/{name}/rrsets/{subname}/{type}/`),
  - `route53.py` (boto3 `client.change_resource_record_sets`).
- Общий интерфейс: `class DnsProvider: async def add_txt(name, value); async def remove_txt(name)`.
- Маппинг `DnsProvider` enum (из `shared/schemas.py`) → класс через registry.

### 2. Renewal-trigger в `backend/agent/scheduler.py::_check_cert_expiry_job`

**Состояние:** `logger.warning("сертификат истекает через {} дней — нужен renewal")` и всё. Дальше ничего не происходит.

**Что сделать (после ACME):**
- Renewal делает control-plane: scheduler control-plane'а стучит на `/v1/status`, видит `tls_mode=acme` и близкое истечение через свою `TlsConfigRow`, шлёт `/v1/tls/apply` с сохранённым в БД конфигом. Состояние в одном месте.

### 3. Первый GitHub Release wheel'а агента

**Состояние:** `AGENT_WHEEL_URL` (default в `backend/server/config.py`) указывает на ещё не существующий `https://github.com/waygate/waygate/releases/latest/download/waygate_agent-py3-none-any.whl`. Provisioner упадёт на `curl wheel` шаге.

**Что сделать:**
- Создать тег `agent-v0.1.0`, `.github/workflows/release-agent.yml` соберёт wheel и запушит в Release.
- После — обновить default `AGENT_WHEEL_URL` на конкретный URL версии и `deploy/.env.example`.

### 4. Multi-user UI (создание/удаление юзеров через панель)

**Состояние:** есть только bootstrap первого админа через ENV. Дополнительные юзеры создаются вручную: SQL INSERT в БД или через REST если открыть эндпоинт.

**Что сделать:** `POST /api/v1/auth/users` (admin-only), `GET /api/v1/auth/users`, `DELETE /api/v1/auth/users/{id}`, `PATCH /api/v1/auth/users/{id}` (смена пароля / деактивация). Frontend-страница «Пользователи» в settings/sidebar.

### 5. httpOnly cookie + CSRF вместо localStorage

**Состояние:** session-JWT хранится в `localStorage` (`waygate-auth`). Это XSS-attack-surface — любой инжект может прочитать токен.

**Почему пока так:** для MVP проще для SPA + Playwright. localStorage — стандартный паттерн для прототипов.

**Что сделать:** Backend ставит `Set-Cookie: waygate_session=<jwt>; HttpOnly; Secure; SameSite=Lax`. Frontend убирает Authorization-header — браузер автоматически отправляет cookie. Добавить CSRF-токен: возвращать в JSON-теле логина + проверять в middleware на mutations. Auth-store во фронте перестаёт хранить токен, только `user`.

### 6. Backups Postgres

**Состояние:** `deploy/docker-compose.yml` не настраивает дампы. Volume `pgdata` есть, но если он умрёт — всё пропало.

**Что сделать:** sidecar-сервис в compose (`postgres-backup-s3` image или cron-job-контейнер с `pg_dump` в S3/локальный путь) + раздел в `README.md`.

### 7. TLS-by-default в edge nginx + certbot

**Состояние:** `deploy/nginx.conf` слушает только :80 — TLS-блок закомментирован, ждёт `tls/cert.pem` + `tls/key.pem`. HSTS не выставляется.

**Что сделать:** certbot-companion-контейнер для Let's Encrypt самой панели, HTTPS-only по умолчанию (80 → 301 на 443), HSTS-заголовок в 443-блоке.

### 8. Дополнительное e2e-покрытие

**Состояние:** есть 6 базовых тестов (auth, server-add-with-error, server-crud-via-rest). Остальные табы (rules/dns/geoip/metrics/tunnels) не покрыты.

**Что сделать:** новые spec-файлы `rules.spec.ts`, `dns.spec.ts`, `geoip.spec.ts`. Сценарии: создать GeoIP-список → создать routing rule → apply → увидеть событие через WS.

### 9. Tweaks-функционал — расширение

**Состояние:** тема (dark/light) и sparklines уже работают через Zustand persist + переключатели в Topbar.

**Что можно ещё добавить (если понадобится):** настройка интервала polling метрик, выбор плотности (compact / cosy), кастомные цветовые акценты.

### 10. Хранение SSH-кредов при онбординге

**Состояние:** не сохраняем — `ProvisionRequest` поля `ssh_password`/`ssh_private_key` приходят, передаются в `run_provision` и уходят в GC. UI-копи поправлен и говорит правду.

**Что сделать (опционально):** если нужна reprovision-функция без участия оператора — шифровать через `cryptography.fernet` ключом из `SECRET_KEY` и хранить в `Server.encrypted_ssh_creds`. Для MVP политика «минимум attack surface» оставлена.

---

## Что уже закрыто (для истории)

### Auth-система control-plane
- ✅ **Username/password + bcrypt + JWT** (Variant B). `User` модель + миграция + bcrypt(12 rounds). Сессионный JWT через `server/auth/session.py`, FastAPI-dependency `require_user` принимает Bearer-header или `?access_token=` query-param (для EventSource). Глобально защищены все `/api/v1/*` кроме `/auth/login`. Bootstrap первого админа из ENV (`WAYGATE_ADMIN_USER`/`WAYGATE_ADMIN_PASSWORD`) в lifespan.
- ✅ **Frontend login flow** — Zustand persist-стор `waygate-auth`, LoginPage, App-guard, кнопка «Выход» в Topbar. `client.ts` добавляет Authorization header, на 401 от `/auth/me` чистит стор.
- ✅ **Audit-middleware** теперь пишет `username` из session-JWT.

### e2e Playwright
- ✅ **Конфиг + 6 тестов**: auth (4 — форма/wrong/login/logout), server-onboarding (1 — error-flow с retry-кнопкой), server-crud (1 — REST→reload→sidebar). Реальный backend (sqlite e2e_test.db, мигрирующийся при старте), реальный frontend (`vite dev` с proxy). Время прогона ~7-8 секунд.
- ✅ **CI-job** в `.github/workflows/ci.yml`: установка Playwright browsers, прогон, upload report при failure.

### Реальные пробелы по SPEC
- ✅ **`/v1/dns/apply` на агенте** + `agent/dns.py` (генерация `/etc/dnsmasq.d/waygate.conf`, идемпотентный reload, 4 теста)
- ✅ **`/v1/token/rotate`** на агенте + `POST /api/v1/servers/{id}/token/rotate` на сервере + кнопка в `Topbar`
- ✅ **Healthcheck-таска** отдельно от metrics-poller'а (`tasks/healthcheck.py`, интервал 60с)

### Технический долг
- ✅ **`redirect_slashes=False`** в FastAPI-app — никаких лишних 301
- ✅ **Regex-валидаторы** для `ipset_name`, `via_interface`, `country`, `domain` через `Annotated[str, StringConstraints]`
- ✅ **Security headers** в edge nginx: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- ✅ **Rate limiting** в edge nginx: `limit_req_zone` для `/api/v1/auth/ws-token`, `/api/v1/servers/provision`, общего `/api/`
- ✅ **SYSTEMD drift** — `agent.service` теперь живёт в `backend/server/provisioner/agent.service` как package-data, читается через `importlib.resources`. Inline-шаблон удалён
- ✅ **Богатый `/v1/tunnels` дотянут до фронта** — серверный proxy `GET /api/v1/servers/{id}/tunnels`, `useTunnels`, перерисованный `TunnelsTab.tsx` с таблицей пиров (rx/tx/handshake age/short pubkey/stale-индикация)
- ✅ **Tweaks** (тема + sparklines toggle) — Zustand persist + переключатели в Topbar
- ✅ **Автоген TS-типов в CI без поднятия backend** — `dump_openapi.py` через `app.openapi()`, drift-check в CI на `server/openapi.json` и `frontend/src/api/openapi.ts`
- ✅ **AddServerModal: не уходим на «Готово» при ошибке** — терминал-state определяет UX, при error видны Повторить/Назад, при done — Дальше

### Production-readiness
- ✅ **Prometheus** — `prometheus-fastapi-instrumentator` на агенте и сервере, `/metrics` экспортит request rate/latency/in_progress
- ✅ **Audit log** — `AuditEntry` модель + миграция + ASGI-middleware (POST/PATCH/DELETE/PUT с redacted-payload, только успешные) + `GET /api/v1/audit?range=1h|24h|7d&server_id=X`. Sensitive-ключи (`password`, `cert_pem`, `key_pem`, `dns_api_key`, `token`) → `***`

### Прочее
- ✅ **Текст в AddServerModal** про SSH-креды поправлен — теперь правда: «не сохраняются»
