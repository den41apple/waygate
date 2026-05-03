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

### 11. Edit-форма для существующих правил/DNS

**Состояние:** правила/DNS-записи создаются (модалки), удаляются и toggle'ятся. Полноценная **edit-форма** (изменение IP/маски/доменов уже существующей записи) пока только через REST/curl. Для UX-полноты можно добавить инлайн-edit на карточке (или открывать `AddRuleModal` в режиме edit). Не критично — через UI можно delete + create.


---

## Что уже закрыто (для истории)

### Auth-система control-plane
- ✅ **Username/password + bcrypt + JWT** (Variant B). `User` модель + миграция + bcrypt(12 rounds). Сессионный JWT через `server/auth/session.py`, FastAPI-dependency `require_user` принимает Bearer-header или `?access_token=` query-param (для EventSource). Глобально защищены все `/api/v1/*` кроме `/auth/login`. Bootstrap первого админа из ENV (`WAYGATE_ADMIN_USER`/`WAYGATE_ADMIN_PASSWORD`) в lifespan.
- ✅ **Frontend login flow** — Zustand persist-стор `waygate-auth`, LoginPage, App-guard, кнопка «Выход» в Topbar. `client.ts` добавляет Authorization header, на 401 от `/auth/me` чистит стор.
- ✅ **Audit-middleware** теперь пишет `username` из session-JWT.

### Scope-маршрутизация (host / container) — двойной VPN
- ✅ **`RoutingScope` enum** (`shared/schemas.py`): `host` (default) и `container`. `RoutingRule` дополнен полями `scope` и `scope_target` (имя docker-контейнера).
- ✅ **SQLModel `RoutingRule`** + alembic миграция `add_column scope/scope_target` с дефолтом `"host"` для существующих записей (zero-downtime).
- ✅ **`agent/routing.py` рефакторинг**: единый `_apply_rules_in_scope` принимает `_ScopeContext` с `command_prefix`. Для host — пустой prefix, для container — `["nsenter", "-t", str(pid), "-n"]` (PID контейнера резолвится через `docker inspect`). Группировка `_group_by_scope` — несколько правил с одним target шерят один контекст. Каждый netns обрабатывается независимо: своя idempotent diff-логика для iptables/ip rule/ip route.
- ✅ **Control-plane API** (`server/api/rules.py`): `RuleCreate`/`RuleUpdate`/`RuleResponse` принимают/отдают `scope` + `scope_target`. Старые клиенты без scope продолжают работать (default = host).
- ✅ **Frontend `AddRuleModal`**: tab-switcher «Хост (вся ВМ)» vs «Контейнер (внутри netns)». При выборе container — select из `useTunnels(serverId).tunnels[*].container_name`. На карточке правила (`RoutingTab`) — pill `host` или `🐳 amnezia-awg2`.
- ✅ **Тесты**: 2 новых на routing (container-сценарий с `nsenter`, изоляция host/container в одном batch'е) и 2 на rules-API (CRUD со scope=container, default scope=host).
- **Pre-conditions для прода**: на target нужен `nsenter` (часть `util-linux` — есть везде кроме совсем минималистичных образов) и привилегии `NET_ADMIN`/`SYS_ADMIN` для входа в чужой netns. Агент уже работает как root через systemd, дополнительной настройки compose не требуется.

### AmneziaWG-клиенты — managed deployment через UI
- ✅ **Свой docker-образ `waygate-awg-client`** (Dockerfile в `backend/agent/awg-client.Dockerfile`, alpine + amneziawg-tools/-go + iptables + tini). CI собирает и пушит в GHCR. Workflow `.github/workflows/release-awg-client.yml`.
- ✅ **Парсер `.conf` AmneziaWG 2.0** (`backend/shared/awg_config.py`) — все 19 параметров `[Interface]` (Address/DNS/PrivateKey/Jc/Jmin/Jmax/S1-S4/H1-H4/I1-I5) + 5 параметров `[Peer]` + валидация base64-ключей и Endpoint. 14 юнит-тестов на edge-cases.
- ✅ **Шифрование конфигов в БД** (`backend/server/auth/secrets.py`) — Fernet с ключом через HKDF из `SECRET_KEY`. Конфиг с PrivateKey не лежит в БД plaintext'ом.
- ✅ **Agent endpoints `/v1/clients/*`** (`backend/agent/awg_clients.py`) — POST (deploy с автоустановкой docker через `get.docker.com` если нет), GET list, /start, /stop, DELETE, /qr (через `qrencode`), /config. Контейнеры с label `io.waygate.role=client`, имя `waygate-amnezia-client-<name>`. Auto-restart через `--restart unless-stopped`.
- ✅ **Control-plane CRUD** (`backend/server/api/clients.py`) — `POST /servers/{id}/clients`, GET list, DELETE, /start, /stop, /config (декрипт + `Content-Disposition: attachment`), /qr (прокси PNG). Модель `AwgClient` + alembic-миграция.
- ✅ **WS-events**: `awg_client.created/.deleted/.status_changed` — фронт инвалидирует список без F5.
- ✅ **Frontend**: модалка `AddAwgClientModal` с drag-n-drop `.conf`-файла, preview распарсенных полей, валидация на лету. Карточки клиентов в `TunnelsTab` с кнопками Start/Stop/QR/Скачать .conf/Удалить + модалка просмотра QR.
- ✅ **Detection role** (`backend/agent/tunnels.py`): `AwgContainerInfo` теперь содержит `role` (`client` если наш label, `external` для user'ских контейнеров).
- ✅ **Audit**: `_SENSITIVE_KEYS` расширены на `config_text`, `PrivateKey`, `PresharedKey`.
- ✅ **Тесты**: 14 на парсер, 5 на Fernet, 6 на агентский deploy/list/lifecycle, 7 на control-plane CRUD + шифрование round-trip, 1 e2e (открытие модалки + drag-n-drop сценарий с интерсептом запроса).

### Re-онбординг + удаление сервера в UI + timezone в metrics_poller
- ✅ **Upsert по host в `POST /servers/provision`** — повторный онбординг на тот же IP/DNS обновляет существующий `Server`-record вместо создания дубликата. Связанные `rules/dns/metrics/tls` сохраняются. Тест `test_provision_reuses_existing_record_for_same_host` зафиксировал поведение.
- ✅ **Кнопка удаления сервера в Sidebar** — `<button class="sb-del">` с иконкой `x`, видна на hover-карточке. `window.confirm()` с именем/host для подтверждения. Сбрасывает `activeServerId` если удалён активный сервер. Через `useDeleteServer` → WS-event `server.deleted` → invalidate → исчезает из списка.
- ✅ **Timezone-fix в `metrics_poller`** — `snapshot.timestamp` от агента приходит aware (Pydantic ISO-8601), стрипается в naive UTC перед записью в `TIMESTAMP WITHOUT TIME ZONE` колонку. asyncpg больше не падает с `can't subtract offset-naive and offset-aware datetimes`. Retention-cleanup cutoff остался naive (унифицировано).

### UI-формы для CRUD ресурсов
- ✅ **`AddRuleModal`** (`frontend/src/modals/AddRuleModal.tsx`) — country (ISO-2), ipset, fwmark, table_id, via_interface (select из `awg_containers`), via_gateway, enabled-toggle. Подключена к `useCreateRule()`.
- ✅ **`AddDnsModal`** — name, domains (textarea, parse по `\n`), ipset, enabled-toggle. Через `useCreateDnsRule()`.
- ✅ **`AddGeoListModal`** — country (ISO-2), name, source_url. Auto-fill URL для ipdeny.com при вводе страны. Через `useCreateGeoList()`.
- ✅ **`UpdateAgentModal`** — version, wheel_url (default — `/latest/download/...`), wait_for_reconnect-toggle. Открывается из Topbar. Через `useUpdateServer()`.
- Кнопки `+ Добавить ...` на табах теперь живые. `TunnelsTab` остался read-only (тоннели создаются через docker outside-of-scope).

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
