# 07 — Типобезопасность и codegen (schema as source of truth)

Принцип: **никаких ручных TS-DTO и ручных списков event-типов.** Бэкенд-схемы — единственный
источник; фронт-типы **генерируются**; дрейф ловится в CI. Донор: `backend/server/scripts/`,
`frontend/src/api/`, `.github/workflows/ci.yml`.

## Две codegen-цепочки

### 1. REST-контракт: Pydantic → OpenAPI → TS

```
Pydantic-схемы (server/api/*, shared/schemas.py)
   │  uv run python -m server.scripts.dump_openapi
   ▼
backend/server/openapi.json     ← КОММИТИТСЯ в репо
   │  npm run generate-types  (openapi-typescript)
   ▼
frontend/src/api/openapi.ts     ← КОММИТИТСЯ, не редактировать руками
```

- `dump_openapi.py` дёргает `app.openapi()` и пишет JSON — **без поднятия живого backend'а**
  (поэтому годится для CI). _Не забыть выставить `SECRET_KEY` в ENV — иначе config.py уронит импорт._
- `openapi.json` **коммитится**; CI перегенерит и упадёт, если он разошёлся с кодом.

### 2. WS-события: Python Enum → TS-литералы

```
backend/server/ws/events.py::EventType (StrEnum)
   │  uv run python -m server.scripts.dump_ws_events
   ▼
frontend/src/api/wsEventTypes.gen.ts   ← TS-union литералов, не редактировать руками
```

## CI drift-check (ключевая дисциплина)

**в доноре: `.github/workflows/ci.yml`**

В CI:
1. Регенерировать `openapi.json` → `git diff --exit-code server/openapi.json` (fail если stale).
2. Регенерировать `openapi.ts` → `git diff` (fail если stale).
3. (Желательно) то же для `wsEventTypes.gen.ts`.

Так **невозможно** случайно поменять Pydantic-схему или `EventType` и забыть обновить фронт —
сборка покраснеет.

## Разделение «генерится» vs «руками»

- **Генерится (low-churn, машинно):** `openapi.ts`, `wsEventTypes.gen.ts`. Источник — Pydantic/enum.
- **Руками (high-churn, осмысленно):** `frontend/src/api/types.ts` — ручные удобные типы поверх
  (напр. `WsEvent = {type: WsEventType, payload, server_id, timestamp}` — union собирается из
  сгенерённого `WsEventType`, а обвязка пишется руками).

## Артефакт-pass в CI

Чтобы фронт-джоб не регенерил заново: backend-джоб генерит `openapi.json`, кладёт артефактом,
фронт-джоб (`needs: backend`) его забирает. Цепочка через `needs:` — параллелит и не дублирует работу.

## Reuse-уроки

- Контракт между сервисами — **из одного источника** (Pydantic / Python enum), всё остальное генерится.
- Сгенерённые файлы **коммитятся** и проверяются на дрейф в CI (а не «генерим при сборке и забыли»).
- Правишь схему/enum → прогони codegen → закоммить результат. Любой ручной правки сгенерённого файла
  быть не должно.
