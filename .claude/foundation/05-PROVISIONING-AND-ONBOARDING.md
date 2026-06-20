# 05 — Провижининг и онбординг хостов по SSH

**Главный фокус нового проекта.** Как control-plane разворачивает агента на свежем хосте по SSH,
стримя прогресс в UI. Донор: `backend/server/provisioner/`, `api/provision.py`.

## SSH-слой (asyncssh)

**в доноре: `backend/server/provisioner/ssh.py`**

- `asyncssh.connect()` → переиспользуемое `SSHClientConnection`, обёрнутое в `SshSession` с
  удобными методами. Поддержка и password-, и private-key-auth.
- **sudo через `sudo -n sh -c '<cmd>'`** (а не просто `sudo <cmd>`) — чтобы редиректы/пайпы
  интерпретировались **под** sudo. `-n` = non-interactive (упадёт, если нужен пароль, а не зависнет).
- **Бинари (wheel и т.п.) — через SFTP** (`start_sftp_client()`), не через heredoc.
- **Конфиги — через `write_file()`** (cat-heredoc). ⚠️ **Нормализуй trailing-newline**: если контент
  заканчивается на `\n`, heredoc добавит лишний — был баг. `write_file` это чинит.

## Шаги провижионера

**в доноре: `backend/server/provisioner/steps.py`**

- Установка зависимостей пакетным менеджером.
- **`update-alternatives --set iptables /usr/sbin/iptables-nft`** (+ ip6tables) — на свежей
  Ubuntu 24.04 alternative по дефолту `legacy`, и правила агента уходят в legacy-таблицу, которую
  Docker 28+ игнорирует (см. `10` и `11` — это была дорогая граблина). Idempotent: проверять текущее
  через `readlink`/`--query`, `check=False` чтобы не-Debian не падал.
- **systemd-unit грузится через `importlib.resources`** (`files("server.provisioner").joinpath(
  "agent.service").read_text()`) — юнит едет вместе с wheel'ом, **единый источник истины**, не
  расходится с задеплоенным. _(Копия в `deploy/agent.service` — только для ops-конвенции; править
  оба или хотя бы canonical.)_

## Стримящийся ProvisionJob (live-лог в UI)

**в доноре: `backend/server/provisioner/registry.py`, `service.py`, `api/provision.py`**

- Онбординг — фоновая таска, прогресс идёт как **`ProvisionJob`**, который **broadcast'ит каждый
  шаг и в SSE (per-job подписка), и в WS (глобально)**. Фронт показывает живой лог в модалке.
- SSE-эндпоинт — JWT через `?access_token=`, терминальное событие `end` ≠ успех (см. `03`).
- nginx для provision-stream: `proxy_buffering off`, длинный timeout (см. `09`).

## Идемпотентность онбординга

**в доноре: `api/provision.py` (upsert по host)**

- `POST /servers/provision` делает **upsert по `host`**: повторный онбординг того же IP/DNS обновляет
  существующий `Server`-record, а не плодит дубликат. Связанные сущности сохраняются. (Для этого на
  `Server.host` есть индекс.)

**Reuse-урок:** онбординг должен быть переноборабельным без накопления мусора — ключуй по
стабильному идентификатору хоста.

## Хранение SSH-кредов

**в доноре: `ProvisionRequest` / `Server` model, `auth/secrets.py`**

- **По умолчанию НЕ храним** `ssh_password`/`ssh_private_key` — приходят, используются в
  `run_provision`, уходят в GC. Политика «минимум attack surface». UI-копи говорит правду.
- **Опционально** (для reprovision/диагностики без оператора) — шифровать через `cryptography.fernet`
  ключом, выведенным из `SECRET_KEY` (HKDF), и хранить в `Server.encrypted_ssh_creds`. Тот же Fernet
  используется для шифрования VPN-конфигов в БД (`auth/secrets.py`).

## Что положить в основу нового проекта (где фокус — деплой)

1. **Один SSH-flow** (`SshSession`) с sudo-обёрткой, SFTP для бинарей, write_file для конфигов.
2. **Шаги как данные** (список идемпотентных шагов) + стрим прогресса через job → SSE+WS.
3. **Юнит-файлы/шаблоны — через `importlib.resources`**, не инлайн-строками (single source of truth).
4. **Подготовка системы до установки** (alternatives, зависимости) — иначе агент «вроде ставится»,
   но не работает (см. shadow-chain в `11`).
5. **Upsert по host** + честная политика кредов.
