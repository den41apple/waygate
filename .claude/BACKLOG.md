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

### 11. Edit-форма для существующих DNS / GeoIP / IPset / Direction

**Состояние:** все эти сущности создаются (модалки) и удаляются; backend `PATCH` есть для всех (включая `RoutingDirection`). UI **edit-формы** ещё нет — нужно либо открывать AddXxxModal в режиме edit (с заполненными полями + кнопкой «Сохранить» вместо «Создать»), либо инлайн-edit на карточке. Не критично — через UI можно delete + create.

### 12. ipset_group.* WS-events на бекенде

**Состояние:** фронт-handler в `useWS.ts` для `ipset_group.created/updated/deleted` подготовлен (закомментирован), но backend не шлёт эти события — после CRUD `IpsetGroup` фронт обновляется только через ручной refetch / открытие таба. Не критично, т.к. правок IPset мало и они идут через свою модалку с `invalidateQueries`.

**Что сделать:** в `server/api/ipset_groups.py` после `session.commit()` слать `WsEvent(type=EventType.IPSET_GROUP_CREATED, ...)` через `get_manager().broadcast(...)`; добавить три enum-значения в `server/ws/events.py::EventType`; добавить три литерала в `frontend/src/api/types.ts::WsEventType`; раскомментировать handler в `useWS.ts`.

### 13. Self-update агента сломан: `_SWAP_LOG_PATH` и `_SWAP_SCRIPT_PATH` в недоступных каталогах

**Состояние (CRITICAL — блокирует update flow):** `agent.service` имеет `ProtectSystem=strict` + `ReadWritePaths=/etc/waygate /etc/dnsmasq.d /var/lib/waygate-agent` + `PrivateTmp=true`. У `agent/updater.py` две константы попадают в недоступные каталоги:

- `_SWAP_LOG_PATH = /var/log/waygate-update.log` — не в `ReadWritePaths`, `exec >>"$LOG"` падает с `OSError: [Errno 30] Read-only file system`.
- `_SWAP_SCRIPT_PATH = /tmp/waygate-update-swap.sh` — при `PrivateTmp=true` /tmp приватный для процесса агента; `systemctl restart` гибнет namespace → файл недоступен → swap-скрипт обрывается посередине exec'а. Это была причина update-timeout'ов у пользователя в раннем тестировании.

**Что сделать:** переехать обе константы на `/var/lib/waygate-agent/` (writable, persistent, не приватный):
- `_SWAP_LOG_PATH = /var/lib/waygate-agent/update.log`
- `_SWAP_SCRIPT_PATH = /var/lib/waygate-agent/update-swap.sh`

После этого self-update должен работать end-to-end. Опционально вынести оба в `agent/config.py::data_dir` чтобы переопределялись через ENV.

### 13a. Накопленные agent-правки в repo не дошли до сервера `den41`

**Состояние:** в repo с момента последнего деплоя агента накопились критичные правки в `agent/`:
1. `awg_clients.py::deploy_client` — `docker pull` перед `docker run` (без него локальный кеш image устаревает).
2. `awg_clients.py::deploy_client` — `--privileged` (sysctl `src_valid_mark` иначе падает на ro-`/proc/sys`).
3. `shared/awg_config.py::serialize_awg_config` — принудительный `Table = off` (без него awg-quick hijack'ит дефолтный маршрут хоста и обрубает SSH).
4. `agent/ipset.py::apply_custom_ipset` — одинаковые параметры tmp/целевого set'а (идемпотентность повторного `apply`).

И в unit-файле:
5. `agent.service` — `/etc/dnsmasq.d` в `ReadWritePaths` (DNS-prereq при apply rules).

Сервер `den41` сейчас работает на старой версии: AWG-клиент **опасен в использовании** (без `Table = off` запуск hijack'ит SSH), DNS-apply падает на read-only `/etc/dnsmasq.d/`.

**Что сделать (в этом порядке):**
1. Сначала #13 (фикс update-paths) — иначе self-update не доедет до сервера никогда.
2. Бамп версии в `backend/agent/pyproject.toml`, тег `agent-v0.2.0`, push → workflow `release-agent.yml` соберёт wheel и опубликует.
3. Серверу `den41` — manual catch-up:
   - scp нового unit'а в `/etc/systemd/system/waygate-agent.service` + `daemon-reload`.
   - Через UI Topbar → Update Agent → ввести `0.2.0` (запустит self-update flow с workaround'нутыми путями).
4. После — снять с BACKLOG `#3` (первый GitHub Release wheel'а), `#13`, `#13a`, `#15` (workaround на ipset already exists), `#14` (agent/dns.py создаёт ipset).

### 14. `agent/dns.py` должен создавать ipset'ы перед reload dnsmasq

**Состояние:** dnsmasq НЕ создаёт ipset'ы автоматически — он только пишет резолвы в существующие. Если ipset не создан заранее, iptables `--match-set <name>` падает с `Set <name> doesn't exist`. Сейчас server-side workaround в `server/api/rules.py::apply_rules`: перед `apply_dns` для каждого DNS-rule дёргается `/v1/ipset/apply` с пустыми cidrs — это создаёт пустой ipset через `ipset create -exist`. Лишний round-trip для каждой DNS-rule.

**Что сделать:** в `agent/dns.py::apply_dns` добавить шаг «создать ipset если нет» перед reload dnsmasq — `ipset create -exist <name> hash:ip family inet`. После этого workaround на server'е удалить.

### 15. agent/ipset.py: убрать server-side workaround на «already exists»

**Состояние:** баг идемпотентности был — `apply_custom_ipset` создавал tmp set с `hashsize/maxelem`, а целевой — без, после swap параметры расходились, повторный `create -exist` падал с "Set cannot be created: set with the same name already exists". В коде агента исправлено (одинаковые параметры в обоих create), но `server/api/rules.py::apply_rules` пока ловит и игнорирует эту ошибку чтобы работать со старыми wheel'ами агента.

**Что сделать:** после релиза нового wheel'а агента, удалить `try/except` вокруг `apply_custom_ipset` в `server/api/rules.py` — фикс агента сам обеспечит идемпотентность.

### 16. Reconcile AwgClient.status ↔ реальное состояние docker-контейнера

**Состояние:** `AwgClient.status` в БД показывает `running`, но соответствующего docker-контейнера на агенте может не быть (например, кто-то сделал `docker rm` руками или контейнер вылетел на старте и не оставил трупа). UI на основе stale-БД показывает «active» — пользователь думает что всё ОК, потом получает «netdev awg-X не найден» при apply routing-правил.

**Что сделать:** добавить в healthcheck-таску (`server/tasks/healthcheck.py` или новая периодическая reconcile-таска) — для каждого `AwgClient` дёргать `/v1/clients/{name}/status` на агенте, агент проверяет `docker inspect` и возвращает реальное состояние; server обновляет `AwgClient.status` если расходится. Также — broadcast WS-event `awg_client.status_changed` чтобы UI обновился без F5.

### 17. AmneziaWG-клиент должен работать с `Table = off` (отключить hijack дефолтного маршрута)

**Состояние (КРИТИЧНО):** awg-quick по дефолту с `AllowedIPs = 0.0.0.0/0` и `Table = auto` создаёт `ip rule not fwmark 51820 table 51820` + `ip route 0.0.0.0/0 dev awg-X table 51820` — это hijack всего трафика хоста через VPN. Если туннель не поднялся (handshake fail), хост теряет связь — у пользователя `den41` SSH вылетел при первом успешном запуске awg-quick с `--privileged` (раньше awg-quick откатывался на sysctl-ошибке, теперь с privileged идёт до конца и оставляет broken hijack).

**Архитектурное противоречие:** Waygate использует AWG-клиент как просто **netdev на хосте**, а маршрутизацию делает сам через `apply_rules` (уникальные fwmark/table_id из RoutingDirection). Hijack от awg-quick лишний и опасный.

**Что сделать:** при сохранении `.conf` на агенте автоматически дописывать `Table = off` в секцию `[Interface]`. Это документированная опция wg-quick: netdev/IP/peer/sysctl поднимаются, ip rule/ip route не трогаются. Поправка в `agent/awg_clients.py::deploy_client` ИЛИ в `shared/awg_config.py::serialize_awg_config`. После релиза нового wheel'а + перезапуска клиентов с `--privileged` + `Table=off` всё должно работать.

**Дополнительно:** в `agent/awg_clients.py::deploy_client` добавить `--privileged` (sysctl `src_valid_mark` иначе падает на read-only `/proc/sys`). Без `Table=off` privileged смертельно опасен — поэтому два изменения уходят в один релиз.

### 18. Real-Docker integration в CI (release-pipeline)

**Состояние:** integration-тесты есть локально (`agent/tests/test_integration.py`, маркер `@pytest.mark.integration`, по умолчанию выключены в `addopts`). Поднимают `--privileged`-контейнер с ipset/iptables/dnsmasq, гоняют HTTP к живому granian'у; 3 теста — smoke status, dns-apply пишет конфиг, ipset idempotency. Локально ~63 сек (build+run), на macOS Docker Desktop работает.

**Что сделать в CI:** добавить отдельный job в `.github/workflows/ci.yml` (или новый `.github/workflows/agent-integration.yml`) — запускает только при пуше тега `agent-v*` или label на PR'е. На GitHub-runners (`ubuntu-latest`) docker daemon доступен из коробки. Команда: `cd backend && uv run pytest -m integration`.

Не включать в каждый PR — build+run образа долгий, а unit-тесты с fake_run уже покрывают логику.

### 19. Аннотации параметров в существующих тестах (постепенно)

**Состояние:** mypy `check_untyped_defs=true` включён для тестов; новые тесты пишутся с аннотациями (`client: AsyncClient`, `monkeypatch: pytest.MonkeyPatch` и т.п.). Существующие ~95 функций — без аннотаций параметров, но тела проверяются через `check_untyped_defs`.

**Что сделать:** при следующем касании старого теста — дописать аннотации параметров. Возврат не аннотируем (всегда None). Не делаем массовый refactor одним PR — слишком много merge-конфликтов.

### 20. Унификация AwgClient `name` ↔ `awg-<name>` netdev

**Состояние:** имя netdev'а генерится во фронте (`AddRoutingDirectionModal.tsx`) как `` `awg-${client.name.slice(0, 11)}` `` — это дублирует логику из `agent/awg_clients.py::_iface_name`. Если правила генерации netdev-имён в агенте поменяются (Linux IFNAMSIZ=16, поэтому 15 символов и `awg-`-префикс — фиксированы) — фронт и агент рассинхронизируются.

**Что сделать:** агент возвращать `interface_name` в `/v1/clients` response → фронт берёт оттуда вместо локальной формулы. Уже есть `client.name`, нужен ещё `interface_name`.

### 21a. dnsmasq `no-aaaa` для маршрутизируемых доменов (когда у AWG-туннеля нет IPv6)

**Состояние:** agent v0.2.5 настраивает IPv6-стек (ip6tables, ip -6 rule, `<name>-v6` ipset) для каждого RoutingRule. Это работает только если на awg-клиентском интерфейсе есть IPv6-адрес (`ip -6 addr show awg-X` непустой). У большинства AmneziaWG-серверов IPv6 не настроен — туннель только IPv4. В этом случае:
- dnsmasq получает AAAA-record от upstream → пишет в `dns-youtube-v6`.
- iptables-правило на v6 матчит → fwmark.
- ip -6 rule отправляет в table N → но `default dev awg-nl` без link-local IPv6 на awg-nl → kernel молча дропает пакет.
- curl без `-4` (default IPv6-preferred) падает на TLS handshake.

**Что сделать:** в `agent/dns.py` добавить директиву `no-aaaa` per-domain для маршрутизируемых правил. Тогда dnsmasq возвращает только A для этих доменов → curl всегда через IPv4 → роутинг работает. Пользовательский AAAA-резолв для немаршрутизируемых доменов остаётся.

Альтернатива: если detect'нем что у клиента нет IPv6 (через `ip -6 addr show` на startup'е agent'а), отключать v6-стек глобально per-rule.

### 21. UI-диагностика managed-сервера (через тот же SSH-flow что update)

**Состояние:** когда агент онлайн, но что-то странно ведёт себя (stuck self-update, не применяются rules, нет netdev'ов), сейчас единственный путь — SSH/console руками. Запоминающийся набор команд:

```bash
cat /var/lib/waygate-agent/update.log
ls -la /opt/waygate-agent /opt/waygate-agent.new /opt/waygate-agent.bak 2>&1
systemctl status waygate-agent --no-pager
journalctl -u waygate-agent -n 30 --no-pager
ip rule list | grep fwmark
ipset list -n
ip link show | grep awg
```

**Что сделать:** после реализации SSH-update-flow (хранение SSH-кредов в `Server.ssh_password_encrypted`/`ssh_private_key_encrypted`) добавить кнопку **«Диагностика»** в `Topbar.tsx`. Открывает модалку, выполняет вышеперечисленные команды через тот же `SshSession`, рендерит вывод секциями (как `journalctl` блок, `ls /opt` блок и т.д.). Нет SSH-кредов — кнопка disabled с подсказкой «настрой ssh-credentials».

Дополнительно: кнопка **«Перезапустить агента»** там же — `systemctl restart waygate-agent` через SSH. Полезно для случаев когда агент завис, но restart-button через REST API недоступен (потому что REST API ведёт через зависший process).

---

## Что уже закрыто (для истории)

### Routing-directions редизайн (Sprint 1-4)
- ✅ **Custom IPset как третья сущность** (`backend/server/models/ipset_group.py` + миграция `d8e2f4a17b65`): `IpsetGroup(server_id, name, cidrs JSON)` с UNIQUE(server_id, name); агентский `apply_custom_ipset()` с atomic-swap (`ipset restore` во временный set + `ipset swap`); CRUD-API `/servers/{id}/ipset-groups` с `?apply=true` параметром для немедленного push'а на агента.
- ✅ **`RoutingDirection` (header) + N child-`RoutingRule`'ов** (`backend/server/models/routing_direction.py` + миграция `e92c5b1f3a87`). Direction = «трафик из {GeoIP-зон, DNS-правил, IPset-групп} через VPN-клиента X». В UI пользователь чекбоксит несколько источников — server создаёт по одному `RoutingRule` на каждый источник с **общим** fwmark/table_id и `direction_id=<this.id>`. Это позволяет помечать пакеты разных ipset'ов одной меткой → они все идут в одну routing-таблицу → через один и тот же VPN-туннель.
- ✅ **`agent_client.apply_custom_ipset()`** + WS-события `direction.created/.updated/.deleted` (`server/ws/events.py::EventType`).
- ✅ **Frontend полностью переделан**: 4 главных таба (Routing, Tunnels, Lists, Metrics) вместо 5; `AddRoutingDirectionModal` с multi-select через CheckGroup (Set'ами для O(1) toggle), auto-fill `via_interface`/`via_gateway` из выбранного AWG-клиента, advanced-блок (`<details>`) скрывает scope/iface/gateway/fwmark; RoutingTab с группировкой по AWG-клиенту и бейджами geo/dns/ipset; `TunnelsTab` под-табами Клиенты/Серверные; `ListsTab` под-табами GeoIP/DNS/IPset; `IpsetGroupsTab` отдельная страница для Custom IPset (вынесена из `GeoIpTab`); persist v2 в `store/ui.ts` для миграции `activeTab=geoip|dns → lists`.
- ✅ **Data-migration legacy `RoutingRule` → `RoutingDirection`** встроена в alembic-ревизию `e92c5b1f3a87` через `op.get_bind()` + сырой SQL (без зависимости от ORM-моделей — frozen-snapshot принцип). Группирует по `(server_id, via_interface, via_gateway, fwmark, table_id, scope, scope_target)`; имена `legacy-<iface>` с автоинкрементом при коллизии. Применяется автоматически при `alembic upgrade head` в Dockerfile entrypoint. Smoke-тест на эфемерной SQLite зафиксировал корректность (4 правила → 3 direction'а).
- ✅ **Тесты**: 5 на ipset_groups API, 6 на directions API, 14 на парсер `.conf`, 7 на CRUD AwgClient + Fernet. Total 128 backend tests.
- ✅ **Mypy-override** расширен на `union-attr` (для `RoutingDirection.id.in_()` и подобных SQLModel-column-descriptor паттернов в `server.api.*`/`server.tasks.*`).

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
