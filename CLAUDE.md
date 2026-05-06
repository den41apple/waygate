# Waygate — заметки для AI-ассистентов

Этот файл автоматически подгружается Claude Code при работе в репозитории.
Полная картина — в `.claude/PROJECT_STATE.md`.

## TL;DR

Контрольная панель GeoIP-маршрутизации. Два сервиса: **agent** (на каждом managed
server) и **server** (control-plane), плюс **frontend** (Vite/React). Все 8 фаз
SPEC закрыты + CI/CD + большой кусок техдолга и production-readiness вещей.
Открытые follow-up'ы — в `.claude/BACKLOG.md` (ACME, multi-user UI, бекапы,
расширение e2e).

Защита: username/password → bcrypt + session-JWT. Первый админ создаётся при
старте из ENV `WAYGATE_ADMIN_USER`/`WAYGATE_ADMIN_PASSWORD`.

**218 backend-тестов + 11+ e2e-тестов проходят** (+16 integration с реальным
Docker-контейнером — отключены в `addopts`, гоняются явно через
`uv run pytest -m integration` ~60-90 сек, покрывают ipset/dns/routing.apply +
recovery race + mangle-recovery + dedupe-MASQ + ssh-провижионер end-to-end).
Integration-тесты используют `nft list chain ip <table> <chain>` для проверки
реального netfilter state (а не `iptables -L` который показывает shadow-chain
через iptables-nft compat на Ubuntu 24.04 + Docker 28+). ruff/format/mypy чисто,
frontend typecheck/build зелёные, docker compose поднимается за ~12 сек.

**SECRET_KEY обязателен в проде** (нет fallback'а на dev-default — fail-fast
на старте). В тестах подкидывается через `conftest.py`. Для запуска
`alembic upgrade head` или `dump_openapi.py` тоже нужно выставить
ENV — иначе RuntimeError.

## Что где

- `.claude/SPEC.md` — спецификация (источник истины для контрактов API).
- `.claude/STYLE.md` — стиль кода (Python 3.13, без `from __future__`, StrEnum, русские комментарии).
- `.claude/PROJECT_STATE.md` — текущее состояние, ключевые решения, файловая карта.
- `.claude/ROUTING_ARCHITECTURE.md` — **обязательно при routing-вопросах**:
  какие mangle-цепи (`PREROUTING` only, не FORWARD/OUTPUT), self-bypass'ы,
  scope=host vs scope=container, известные ограничения (двойной NAT через
  bridge, socket-bind mismatch local-TCP, и т.д.).
- `.claude/SESSION_*.md` — снапшоты длинных сессий с открытыми вопросами.
- `.claude/BACKLOG.md` — список follow-up'ов и недоделок (что отложено и почему).
- `.claude/memory/` — feedback и project-заметки между сессиями.
- `README.md` — пользовательская документация (как запустить, как добавить сервер).

## Основные команды

```bash
# Backend: lint + типы + тесты
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q

# Frontend: типы + билд
cd frontend && npm run typecheck && npm run build

# e2e (Playwright). Backend и frontend стартуются автоматически через webServer.
cd frontend && npm run test:e2e

# Production-стек целиком
cd deploy && docker compose up -d --build

# Регенерация TS-типов после изменений Pydantic-схем
cd backend && uv run python -m server.scripts.dump_openapi > server/openapi.json
cd ../frontend && npm run generate-types
```

## Стилевые особенности (вкратце)

- Python 3.13, `int | str | None` вместо `Optional`, `list[int]` вместо `List`.
- StrEnum для всех значимых строк.
- Все ключевые аргументы — keyword-only (`def foo(*, x, y)`).
- Импортируем конечные объекты, не namespace (`from sqlmodel import Field`, не `import sqlmodel`).
- Комментарии и docstring'и — на русском.
- ruff format включён, vertical alignment SPEC'а не сохраняем
  (см. `.claude/memory/spec_is_indicative.md`).

## Архитектурные ограничения, о которых легко забыть

- **TS-типы автогенерируются** — не редактировать `frontend/src/api/openapi.ts` руками.
  Менять Pydantic-схему в `backend/shared/schemas.py` или `server/api/*` → `dump_openapi.py` → `generate-types`.
- **`backend/server/openapi.json` коммитится** — CI проверяет drift.
- **`agent.service`** живёт в **двух** местах: canonical в `backend/server/provisioner/agent.service`
  (читается через `importlib.resources` при онбординге), копия в `deploy/agent.service`
  для ops-конвенции. При изменении — править оба или хотя бы canonical.
- **mypy `arg-type`/`attr-defined`/`union-attr` отключены для `server.api.*`/`server.tasks.*`** —
  это про SQLModel-column-descriptors в `where`/`order_by`/`.desc()`/`.in_()`/`.is_()`.
  Не пытаться их «починить» через type: ignore — это известная боль SQLModel + mypy strict.
- **Audit-middleware best-effort** — пишет с try/except, не должен ломать ответы.
  При добавлении новых sensitive-полей в Pydantic — обновить `_SENSITIVE_KEYS`
  в `backend/server/audit.py`.
- **Auth: session-JWT в localStorage** (`waygate-auth`). XSS — открытое окно.
  Перед прод-релизом — переехать на httpOnly cookie + CSRF (см. BACKLOG).
- **EventSource не ставит auth header** — `require_user` принимает `?access_token=`
  query-param как fallback. Пример использования — `AddServerModal.tsx` SSE.
- **Все `/api/v1/*` (кроме `/auth/login`) защищены global dependency** —
  при добавлении нового роутера не забыть `dependencies=[Depends(require_user)]`
  в `app.include_router(...)`.
- **`RoutingRule.direction_id` ON DELETE CASCADE** прописан и в `sa_column` модели,
  и в alembic-миграции — нужно чтобы каскад работал и при `metadata.create_all` в
  тестах (SQLite), и в проде (Postgres). Если меняешь FK — править оба.
- **Direction CRUD-update** перематериализует child-`RoutingRule`'ы целиком
  (delete + insert) при изменении любого из: `geo_list_ids`, `dns_rule_ids`,
  `ipset_group_ids`, `via_interface`, `via_gateway`, `scope`, `scope_target`,
  `enabled`. Diff'ать сложнее и чреват багами — не оптимизировать преждевременно.
- **Data-migration в alembic** — для one-time data-перетряхивания (как
  `_migrate_legacy_rules` в ревизии `e92c5b1f3a87`) пишем сырой SQL через
  `op.get_bind()`, **не** импортируя ORM-модели. Это frozen-snapshot принцип:
  если в будущем модель переименует поле, миграция всё равно должна работать.
- **TabId migration в `store/ui.ts`** — старые сессии с `activeTab=geoip|dns`
  валидно мапятся в `lists` через `migrate` хук persist v2. При добавлении новых
  табов и удалении старых — обязательно обновить `migrate` или поднять `version`.
- **Integration-тесты агента опциональны** — `agent/tests/test_integration.py`
  с маркером `@pytest.mark.integration` поднимает реальный `--privileged`-контейнер
  (`backend/agent/Dockerfile`). По умолчанию выключены через
  `addopts = "-m 'not integration'"`. Запуск: `uv run pytest -m integration`.
  Ловят то, что моки `fake_run` не ловят: read-only filesystem, отсутствие
  бинарей, реальные ipset-параметры.
- **`agent/Dockerfile` использует `docker-ce-cli` из docker-repo, не `docker.io`** —
  пакет `docker.io` в slim-Debian содержит только daemon (без `/usr/bin/docker`),
  поэтому `docker ps` из агента падал с `FileNotFoundError`.
- **`subprocess_runner.run_command` оборачивает `FileNotFoundError` в `CommandError`** —
  на минималистичных системах (LXC, slim-контейнеры) `systemctl`/`ipset`/etc
  могут отсутствовать. Вызывающим достаточно одного `except CommandError`,
  не нужно отдельно ловить `FileNotFoundError`.
- **При предложении прод-команд думать про сетевые побочки** — `--privileged`
  без `Table=off` в `[Interface]` AmneziaWG hijack'ит default-route → SSH
  обрывается. См. `agent/awg_clients.py` и `shared/awg_config.py::serialize_awg_config`.
- **Routing/iptables правила НИКОГДА не патчить инкрементально** —
  `_MARK_CHAINS = ("PREROUTING",)`, не FORWARD (mark после route lookup'а
  не вызывает reroute) и не OUTPUT (socket-bind mismatch ломает local TCP).
  Полная архитектура — в `.claude/ROUTING_ARCHITECTURE.md`. Перед изменениями
  обязательно прорисовать **полный путь пакета** через netfilter hooks для
  forwarded vs local-originated vs incoming-на-local. Иначе ушёл целый день
  в reactive-mode (см. `.claude/SESSION_2026_05_04.md`).
