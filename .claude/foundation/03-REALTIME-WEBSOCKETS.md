# 03 — Реал-тайм: WebSocket-петля + SSE

**Это та часть, которой особенно стоит гордиться.** Реал-тайм построен на принципе
**«событие → инвалидация», а не «событие → стейт»**: бэкенд шлёт типизированное событие, фронт
инвалидирует нужный `queryKey`, а TanStack Query сам перетягивает свежие данные. Никакого
ручного синка состояния, никакого отдельного event-bus на фронте — **шина = WS, кэш-стейт = Query**.

## Полная петля (end-to-end)

```
[мутация в API или фоновая таска]
        │
        ▼  manager.broadcast(WsEvent(type=EventType.DIRECTION_UPDATED, server_id=…, payload=…))
ConnectionManager (singleton, get_manager())  ── рассылает JSON всем живым WS, мёртвые выкидывает
        │
        ▼  ws://host/ws/events?token=<ws-jwt>
[фронт useWS]  switch(event.type):
        │        case "direction.updated": queryClient.invalidateQueries(directionsKey(serverId))
        │        case "server.metrics":    queryClient.invalidateQueries(SERVERS_KEY)  // бейджи
        ▼
TanStack Query рефетчит инвалидированные ключи → UI обновился без F5
```

## Бэкенд

**в доноре: `backend/server/ws/events.py`, `ws/manager.py`, `ws/router.py`**

- **`EventType` — `StrEnum`** (не голые строки): `server.metrics`, `direction.created/.updated/
  .deleted`, `awg_client.status_changed`, … Опечатка ловится на импорте.
- Каждое событие — `WsEvent(type: EventType, payload: dict, server_id: int | None, timestamp)`.
- **`ConnectionManager` — singleton на уровне модуля**, доступ через `get_manager()` (функция, не
  через `app.state`). Это ключ: **фоновые задачи могут броадкастить, не держа ссылку на `app`**.
- `broadcast()` итерирует подключённые WS, шлёт JSON, дохлые соединения удаляет (resilient).
- WS-аутентификация — по `?token=<ws-jwt>` на старте соединения (отдельный короткий JWT из
  `/api/v1/auth/ws-token`, см. `02`).

**Паттерн emit:** из любого места (роутер после мутации / фоновая таска) →
`get_manager().broadcast(event=WsEvent(...))`. Никакой инфраструктуры передачи `app` не нужно.

## Фронт

**в доноре: `frontend/src/ws/useWS.ts`, `ws/store.ts`, вызывается один раз в `App.tsx`**

- **`useWS` вызывается ОДИН раз в корне** (в `Dashboard`), после валидации сессии. Не пер-компонент.
- На каждый (ре)коннект — **свежий** ws-JWT через `POST /api/v1/auth/ws-token` (не кладём в queryKey).
- Открывает `ws://host/ws/events?token=JWT`, **exp-backoff реконнект** (1s → 15s максимум).
- На каждое событие — **`switch(event.type)` с явным маппингом на `invalidateQueries(<key>)`**.
  Маппинг **явный, не авто-discovery** — так пропущенный handler виден в коде.
- **Каскадные инвалидации:** правка direction'а инвалидирует и routes, и geoip — чтобы бейджи-счётчики
  не протухали.
- **`wsStore` (Zustand, без persist) хранит только `status`** (`connecting|connected|disconnected`)
  для индикатора. **Событий не буферизит** — они потребляются сразу в инвалидацию.

```ts
// эскиз useWS (донор: frontend/src/ws/useWS.ts)
socket.onmessage = (e) => {
  const event: WsEvent = JSON.parse(e.data);
  switch (event.type) {
    case "direction.updated":
    case "direction.created":
    case "direction.deleted":
      queryClient.invalidateQueries({ queryKey: directionsKey(serverId) });
      break;
    case "server.metrics":
      queryClient.invalidateQueries({ queryKey: SERVERS_KEY });
      break;
    // … явный case на каждый EventType
  }
};
```

## Типобезопасность event-типов (codegen)

**в доноре: `backend/server/scripts/dump_ws_events.py` → `frontend/src/api/wsEventTypes.gen.ts`**

Python-`EventType` экспортится в TS-литералы скриптом. Фронтовый `WsEvent.type: WsEventType`
ссылается на сгенерённый union. В CI — **drift-check**: перегенерили, `git diff` не пустой → fail.
Так бэкенд и фронт не разъезжаются по типам событий. Детали — в `07`.

## SSE для длинных операций

**в доноре: `frontend/src/modals/AddServerModal.tsx` + серверный provision-stream**

Для **долгих операций с прогрессом** (онбординг хоста по SSH) — не WebSocket, а **SSE**
(`EventSource`):

- JWT передаётся как `?access_token=` (EventSource не умеет кастомные заголовки; `require_user`
  принимает query-param как fallback).
- Сервер стримит JSON-строки `{type, message, timestamp}` построчно (live-лог шагов).
- **Терминальное событие `{type:"end"}` ≠ успех.** Успех/ошибку трекаем отдельным state'ом
  (`done` / `error`) — UX-развилка «Дальше» vs «Повторить/Назад» зависит от терминального состояния,
  а не от факта закрытия стрима.
- nginx для SSE: `proxy_buffering off` + длинный read-timeout (см. `09`).

**Когда что:** WS — много мелких событий, инвалидация кэша, всегда-он. SSE — одна длинная операция
с потоком прогресса, живёт пока операция идёт.

## Reuse-уроки

- Singleton-`ConnectionManager` через `get_manager()` — броадкаст из фоновых задач без `app`.
- Событие несёт `type` (StrEnum) + `server_id` + `payload`; фронт **инвалидирует**, а не мутирует кэш.
- Один `useWS` в корне; TanStack Query — единственная «шина» обновлений UI.
- Свежий короткий JWT на каждый реконнект; exp-backoff.
- Event-типы генерятся (codegen) + drift-check в CI — фронт/бэк не разъедутся.
