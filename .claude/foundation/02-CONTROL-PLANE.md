# 02 — Control-plane (FastAPI + SQLModel + alembic)

Паттерны центрального сервиса. Донор: `backend/server/`.

## App-композиция

**в доноре: `backend/server/main.py`**

- Старт/стоп — через `lifespan` (async context manager), а не `@app.on_event`. Фоновые задачи
  кладём как `asyncio.Task` на `app.state.server`, в финале гасим с `suppress(asyncio.CancelledError)`.
- **CORS-middleware ставить ДО audit-middleware** (audit должен видеть реальный ответ).
- **`redirect_slashes=False`** — чтобы не плодить 307/301-шум.
- **Глобальная защита роутеров:** `app.include_router(r, dependencies=[Depends(require_user)])` на
  регистрации, а не декоратор на каждом эндпоинте. _Правило: при добавлении нового роутера под
  `/api/v1/*` не забыть `dependencies=[Depends(require_user)]` (кроме `/auth/login`)._
- **Bootstrap первого админа из ENV** (`ADMIN_USER`/`ADMIN_PASSWORD`) в lifespan — idempotent,
  скип если юзеры уже есть.

## Config: fail-fast

**в доноре: `backend/server/config.py`**

`SECRET_KEY` валидируется **на импорте модуля**: если пуст или равен известному dev-дефолту →
`RuntimeError`. **Никакого fallback'а.** Причина: предсказуемый ключ = скомпрометированные
session-JWT и (если хранятся) SSH-креды.

- В тестах `conftest.py` выставляет `os.environ["SECRET_KEY"]` **до** импорта любых `server.*`.
- Для `alembic upgrade` и `dump_openapi.py` ENV тоже надо выставить руками.

**Reuse-урок:** критичный секрет — валидировать на старте, ронять процесс, не прятать в try/except.

## Auth: session-JWT + WS-token

**в доноре: `backend/server/auth/session.py`, `auth/dependencies.py`, `ws/router.py`**

- Session-JWT (HS256, `secret_key`) с claim'ами `sub` (user_id), `username` (для audit), `scope`.
- `require_user` принимает токен из `Authorization: Bearer` **или** `?access_token=` query-param —
  fallback для **EventSource/SSE** (он не умеет слать кастомные заголовки).
- Для WebSocket — **отдельный короткоживущий JWT** (TTL ~1ч) через `POST /api/v1/auth/ws-token`,
  проверяется на старте WS. `scope` разделяет типы токенов (session vs ws), чтобы их не путали.

## SQLModel / alembic-конвенции

**в доноре: `backend/server/models/`, `alembic/versions/`**

- **ON DELETE CASCADE объявлять В ДВУХ местах** — и в `sa_column=Column(..., ForeignKey(...,
  ondelete="CASCADE"))` модели, и в миграции. Иначе каскад не сработает в тестах через
  `metadata.create_all` (SQLite) ИЛИ в проде (Postgres). _Меняешь FK — правь оба._
- **Data-migration = frozen-snapshot.** One-time перетряхивание данных пишем **сырым SQL** через
  `op.get_bind().execute(text(...))`, **не импортируя ORM-модели**. Если модель в будущем
  переименует поле — миграция всё равно отработает.
- **Pivot-таблицы вместо string-magic.** Связи многие-ко-многим — типизированная таблица
  `(parent_id, child_type, child_id)`, а не парсинг имён-строк. Добавление нового типа источника =
  одно enum-значение + handler, без правок reverse-lookup.

## agent_client (исходящие вызовы агента)

**в доноре: `backend/server/agent_client/`**

- `aiohttp.ClientSession` с явными timeout'ами (`connect` + `total`).
- **Tenacity-retry только на read-only** (status/metrics/tunnels/list) — `retry_if_exception_type(
  AgentUnreachable)`, exp-backoff, 3 попытки. **Никогда не retry'ить POST'ы** (apply и т.п.) —
  идемпотентность вызова по сети не гарантирована, повтор может задвоить мутацию.
- Декоратор retry сделан через **`ParamSpec` + `TypeVar`**, чтобы mypy видел сигнатуры декорируемых
  методов (без этого типы схлопываются в `Any`).
- Хелперы `_typed_get(path, Model)` / `_typed_post(path, payload, Model)` — один вызов делает
  запрос + `Model.model_validate()`.
- Ошибка недоступности — `AgentUnreachable` (без суффикса Error; N818 в ignore — читается лучше).

## Фоновые задачи

**в доноре: `backend/server/tasks/metrics_poller.py`, `tasks/healthcheck.py`**

- **Parallel fetch, sequential persist.** Сбор с N агентов — `asyncio.gather(*[fetch(s) for s in
  servers], return_exceptions=True)` (каждый fetch со своей сессией, ранний возврат на ошибке).
  Запись в БД и broadcast — **последовательно** (AsyncSession не concurrent-safe). Один зависший
  агент больше не пинит весь цикл.
- Healthcheck (отдельная таска, интервал ~60с) после `status()` сверяет состояние с БД, при
  расхождении обновляет и шлёт WS-событие. Auto-reapply при OFFLINE→ONLINE с throttle.

## Audit-middleware

**в доноре: `backend/server/audit.py`**

- ASGI-middleware, логирует только мутации (POST/PATCH/DELETE/PUT) и только успешные.
- **Best-effort:** вся запись в `try/except`, ошибка логируется на уровне ERROR (пропавший
  audit-trail — security-relevant), но **не ломает ответ**.
- **Redaction:** значения ключей из `_SENSITIVE_KEYS` (`password`, `cert_pem`, `key_pem`, `token`,
  `config_text`, `PrivateKey`, ...) рекурсивно заменяются на `***` перед записью. _Добавляешь новое
  чувствительное поле в схему — обнови `_SENSITIVE_KEYS`._
- `username` достаётся из session-JWT (best-effort, `SessionTokenError` → None).
