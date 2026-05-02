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

**68 backend-тестов + 6 e2e-тестов проходят**, ruff/format/mypy чисто, frontend
typecheck/build зелёные, docker compose поднимается за ~12 сек.

## Что где

- `.claude/SPEC.md` — спецификация (источник истины для контрактов API).
- `.claude/STYLE.md` — стиль кода (Python 3.13, без `from __future__`, StrEnum, русские комментарии).
- `.claude/PROJECT_STATE.md` — текущее состояние, ключевые решения, файловая карта.
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
- **mypy `arg-type`/`attr-defined` отключены для `server.api.*`/`server.tasks.*`** —
  это про SQLModel-column-descriptors в `where`/`order_by`/`.desc()`. Не пытаться
  их «починить» через type: ignore — это известная боль SQLModel + mypy strict.
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
