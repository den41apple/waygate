# 06 — Фронтенд: API-слой, TanStack Query, Zustand

Vite + React 18 + TS + TanStack Query v5 + Zustand. Донор: `frontend/src/`.

## API-обёртка + ApiError + 401

**в доноре: `frontend/src/api/client.ts`**

- Один тонкий `request<T>()` инкапсулирует весь HTTP: цепляет `Authorization: Bearer` из auth-стора,
  один раз читает тело ответа (стрим читается единожды), на не-2xx бросает `ApiError(status, message,
  body)`.
- **401 обрабатывается в одном месте по канону:** только провал `/auth/me` чистит auth-стор; прочие
  401 просто бросают `ApiError`, а решение о логауте принимает App-guard по `meQuery.error` на
  следующей проверке. Не дёргаем logout на каждый случайный 401.

**Reuse-урок:** весь HTTP — в одном модуле; инвалидация сессии — из единственного источника правды.

## TanStack Query — конвенции

**в доноре: `frontend/src/api/{servers,directions,metrics,...}.ts`**

- **Key-фабрики как `const`:** `export const directionsKey = (id) => ["directions", id] as const`.
  Опечатки в ключах ловятся компилятором, инвалидации не «промахиваются».
- **`select` для решейпа:** хук хранит сырой ответ (`{directions: [...]}`), `select: (d) => d.directions`
  отдаёт компоненту массив. Дешевле, чем дерайвить в компоненте. _(Важно для моков/тестов: сидить
  надо сырой shape под ключ, а `select` его преобразует.)_
- **Мутация → `invalidateQueries`** на `onSuccess`, **не** `setQueryData` руками (инвалидация безопаснее
  ручного патча кэша).
- **Условные запросы:** `enabled: serverId != null` + **стабильный noop-ключ**
  (`["directions", serverId ?? "noop"]`) — не удаляй ключи на лету, выключай запрос.
- **Глобальные дефолты** (`main.tsx`): `staleTime` небольшой, `refetchOnWindowFocus: false`,
  `retry: 1`. Живые данные — opt-in `refetchInterval` пер-хук (метрики/тоннели — 30с).

## WebSocket-петля на фронте

См. `03-REALTIME-WEBSOCKETS` — это сердце реал-тайма. Кратко: один `useWS` в корне; событие →
`switch(type)` → `invalidateQueries`. `wsStore` (Zustand без persist) хранит только `status`.

## Zustand-сторы

**в доноре: `frontend/src/store/{auth,ui,modals}.ts`**

- **`persist` + `version` + `migrate`** для эволюции схемы. Пример (`ui.ts`): старые сессии с
  `activeTab="geoip"` мигрируются в `"lists"`, чтобы UI не падал на невалидном значении. **Всегда
  версионируй persisted-стор; миграцию клади в middleware, не в `useEffect`.** `partialize` исключает
  эфемерное из персиста.
- **Отдельный modals-стор** (`Set<ModalId>` + `show/hide/isOpen`) — чтобы добавление новой модалки
  было одной правкой (enum + строка), а не разрасталось по `ui.ts` и `useState` компонентов.
- **Auth-стор с TTL:** хранит `token`, `user`, `expiresAt` (unix-сек) + метод `isExpired()`.
  App-guard смотрит `meQuery.error` + `isExpired()` и решает Login vs Dashboard. TTL в сторе, не
  только токен.

## SSE-потребление

**в доноре: `frontend/src/modals/AddServerModal.tsx`**

`EventSource` с `?access_token=` (нет кастомных заголовков). Стрим `{type, message, timestamp}`
построчно. Терминальное `end` ≠ успех — терминальный state (`done`/`error`) трекать отдельно, он
определяет UX (кнопки «Дальше» vs «Повторить/Назад»). См. `03`.

## Codegen-файлы — не трогать руками

**в доноре: `frontend/src/api/openapi.ts`, `api/wsEventTypes.gen.ts`**

- `openapi.ts` генерится из бэкендового `openapi.json` (`npm run generate-types`).
- `wsEventTypes.gen.ts` генерится из Python `EventType`.
- **Редактировать только источник** (Pydantic-схема / Python enum), потом перегенерить. Дрейф ловится
  в CI. Детали — `07`.

## Структура UI (для переносимости и дизайн-синка)

- Презентационные компоненты (рисуют на пропсах) **отделять** от контейнеров (тянут данные хуками).
  Это и хорошая архитектура, и условие безболезненного дизайн-синка (см. `11`).
- Тема — через CSS-переменные + `[data-theme]` на `<html>`; обычный CSS (не Tailwind) переносится
  1-в-1 в любой рендерер.
