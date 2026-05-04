# Waygate — архитектура routing'а

Этот документ — единственный источник истины как Waygate **сейчас** строит
trafic-routing. Когда что-то ломается, читать ЭТО, а не гадать.

История версий см. в commit-логе и `BACKLOG.md`. Здесь — текущее состояние.

---

## Use-case'ы

Waygate управляет **forwarded трафиком клиентов** (мак/телефон/iPad) которые
подключаются к target-серверу через свой VPN-конфиг. На target'е стоит
**AmneziaWG-server-контейнер** (вне Waygate, оператор ставит его сам). Когда
клиент подключился, его пакеты попадают в host netns target'а, и Waygate
маршрутизирует их через **AmneziaWG-client-контейнеры** (это уже Waygate-managed,
например `waygate-amnezia-client-firstbyte`, `*-eurohoster`).

Архитектура:
```
phone/mac (с AmneziaVPN-конфигом)
         │
         ▼  UDP-handshake
   target eth0 (yandex VM, hetzner, etc)
         │
         ▼  AWG-server-контейнер расшифровывает
   forwarded TCP/UDP с src=phone_internal_IP, dst=инет
         │
         ▼  Waygate ставит mangle MARK по match-set правилам
   ip rule fwmark X table Y
         │
         ▼  table Y: default dev awg-<X>
   AmneziaWG-client-контейнер (Waygate-managed)
         │
         ▼  WG-инкапсуляция, выход с RU/NL/whatever IP
   зарубежный/RU VPN-провайдер
         │
         ▼
       инет
```

---

## Два scope'а Direction'а

### scope=host

**Когда применять:** AWG-server-контейнер запущен с `--network host`
(или AWG-server без docker'а напрямую на хосте). Тогда forwarded трафик
от клиентов попадает в **host netns**, и waygate ставит mangle/route там же.

**Цепочка iptables (host netns):**

```
mangle PREROUTING:
  1. addrtype --dst-type LOCAL → RETURN  (защита incoming на свой IP — SSH/agent/handshake)
  2. ESTABLISHED → RETURN  (reply'и не маркируются — conntrack ведёт)
  3. tcp --dport 22 → RETURN  (SSH)
  4. tcp --dport <agent-port> → RETURN  (agent-API)
  5. match-set <ipset> dst → MARK <fwmark>  (forwarded → VPN)

routing decision видит mark → ip rule fwmark X → table Y → default dev awg-<client>

POSTROUTING:
  -A POSTROUTING -o awg-<client> -j MASQUERADE  (src переписывается на awg-iface IP)

mangle POSTROUTING:
  TCPMSS clamp без iface-фильтра (clamp всех SYN до PMTU выходного iface'а)
```

**Что НЕ работает в этом режиме:**
- AWG-server в `--network bridge` (двойной NAT через docker NAT + наш MASQUERADE
  → conntrack ломается → reply'и теряются → 0-3 Kbps скорость).
- `local TCP с самого target'а` через mark-routing (socket-bind mismatch:
  curl bind'ится к eth0_IP до OUTPUT mangle, mark переключает на awg-X, reply
  ищет original socket → RST/timeout). Юзер на target'е видит что docker pull,
  apt-get, любой HTTPS handshake таймаутит.

  Workaround для local-curl: `curl --interface awg-X` явно (SO_BINDTODEVICE).

  В 0.2.28 МЫ УБРАЛИ MARK ИЗ OUTPUT — `_MARK_CHAINS = ("PREROUTING",)`.
  Local TCP идёт через main route → eth0. Forwarded по-прежнему через PREROUTING.

### scope=container

**Когда применять:** AWG-server в **bridge** mode или своей netns. Waygate
автоматически переключает AWG-client-контейнер в netns target'а через
`--network container:<имя>`. Iface awg-<client> появляется ВНУТРИ netns
target'а. mangle/route применяются через `nsenter -t <pid> -n`.

**Что делает agent при apply (0.2.21+):**

1. Найти AWG-client'а по via_interface через docker label.
2. Проверить `NetworkMode`: если != `container:<scope_target>`, redeploy через
   `docker stop+rm+run --network container:<scope_target>`.
3. `nsenter` в netns target'а, sync ipset'ы (host → netns через `ipset save | restore`).
4. Применить mangle/rule/route внутри netns (тот же набор что для scope=host).

**Симметрия:** при apply scope=host для тех же via_interface'ов агент
**возвращает** AWG-client'а в host netns (`--network host`). Из этого
следует: **один AWG-client = одна netns** одновременно. Нельзя параллельно
использовать его в host- и container-direction'ах.

**Что НЕ работает (известные ограничения):**
- Orphan-cleanup внутри чужих netns при удалении direction'а
  (см. backlog 0z): mangle/rule/route остаются висеть. Чистить руками.
- Двойная WG-encryption (phone → AWG-server → AWG-client → exit) даёт ~10-20%
  потери throughput'а на 2-vCPU VM.

---

## Self-bypass правила

`_ensure_self_bypass` в `agent/routing.py` ставит в начало PREROUTING/OUTPUT:

| Правило | Зачем |
|---|---|
| `addrtype --dst-type LOCAL → RETURN` (PREROUTING) | Incoming на свой IP не маркируется. Защита от широких catch-all direction'ов которые иначе ловят SSH/agent/AWG-handshake'и (dst=local_IP в `0.0.0.0/1+128.0.0.0/1`). |
| `ctstate ESTABLISHED → RETURN` (PREROUTING+OUTPUT) | Reply на existing connection не маркируется. Без этого incoming reply от инета на forwarded клиент попадал в match-set (dst=phone_IP, может быть в RU-set), уходил в чужой туннель. |
| `tcp --dport 22 → RETURN` (PREROUTING) | SSH self-protection. Дублирует `addrtype LOCAL`, но явное. |
| `tcp --sport 22 → RETURN` (OUTPUT) | SSH-reply self-protection. |
| `tcp --dport <agent-port> → RETURN` (PREROUTING) | Чтобы control-plane мог дёргать агент даже при кривой direction. |
| `tcp --sport <agent-port> → RETURN` (OUTPUT) | Симметрично для reply. |

С 0.2.28 OUTPUT chain не получает MARK от direction'ов, поэтому self-bypass'ы
в OUTPUT теоретически излишни. Оставлены для safety-net.

---

## MASQUERADE

Каждый AWG-client (`via_interface` direction'а) получает на своей out-цепи:

```
iptables -t nat -A POSTROUTING -o awg-<client> -j MASQUERADE
```

Без этого src forwarded-трафика остаётся=phone_internal_IP (10.x.x.x — приватный),
AWG-server (зарубежный VPN-провайдер) дропает как spoofed.

**Конфликт с docker-NAT:** Docker для bridge-сети ставит свой
`-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE`. Когда трафик
от docker bridge → mark → awg-X — Docker SNAT (на eth0_IP) и наш SNAT
(на awg_iface_IP) могут конфликтовать → conntrack reverse теряется → reply'и
не доходят. Это и есть та проблема со scope=host через `--network bridge`
AWG-server.

---

## TCPMSS clamp

```
iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
```

Без iface-фильтра (`-o`) — kernel сам подбирает MSS по PMTU выходного iface'а.
Eth0 (1500): MSS=1460, awg-X (1420): MSS=1380. Без этого TLS ServerHello
~3-5KB фрагментируется на L3, на сетях с блокированным ICMP "frag needed"
дропается → connections виснут.

В 0.2.23 переехали из `OUTPUT` в `POSTROUTING` потому что в OUTPUT pkt
ещё на eth0 (initial route lookup), reroute по mark ещё не произошёл,
и matcher `-o awg-X` не срабатывал.

---

## ip rule + ip route

При apply:
```
ip rule add fwmark <X> lookup <table_id>
ip route replace default dev awg-<client> table <table_id>
```

С 0.2.15 default-route БЕЗ `via <gw>`. AmneziaWG-туннели всегда POINTOPOINT —
ядро само инкапсулирует пакеты, ARP не нужен. Это убрало грабли
"Nexthop has invalid gateway" когда via_gateway случайно совпадал с self-IP.

---

## Известные ограничения

1. **scope=host + AWG-server в `--network bridge`** = двойной NAT → conntrack
   ломается → 0 b/s. Нужно либо переключить AWG-server в `--network host`,
   либо использовать scope=container.

2. **Local TCP с самого target'а через mark-routing** не работает (socket-bind
   mismatch). Workaround: `curl --interface awg-X`. С 0.2.28 OUTPUT-mark выкл,
   local TCP идёт через eth0 без VPN.

3. **catch-all direction (`0.0.0.0/1 + 128.0.0.0/1`)** требует обязательно
   `addrtype --dst-type LOCAL` bypass'а (мы ставим автоматически).

4. **Self-lockout при apply RU-direction на RU-сервере**: до 0.2.13 это рвало
   SSH. Сейчас self-bypass'ы (SSH+agent+ESTABLISHED+LOCAL) защищают.

5. **Orphan iface при delete AWG-client'а в container netns**: до 0.2.24 iface
   оставался жить в чужой netns как orphan. С 0.2.24 `delete_client` смотрит
   NetworkMode и сносит iface через `nsenter`.

---

## Тестирование

Какие команды реально показывают работает ли направление:

```bash
# 1. Direction applied?
sudo iptables -t mangle -L PREROUTING -v -n | grep match-set
# Должно быть: 1+ packets, MARK set 0xN, --match-set <name>-v4

# 2. ip rule + table настроены?
sudo ip rule show | grep fwmark
sudo ip route show table <table_id>

# 3. Forwarded путь работает?
# Запустить тест-контейнер:
sudo docker run --rm --network bridge curlimages/curl curl -sL --max-time 10 https://myip.ru/me
# Должен вернуть IP awg-<client>'а (например eurohoster IP)

# 4. Counter MASQUERADE на awg-iface растёт?
sudo iptables -t nat -L POSTROUTING -v -n | grep awg-

# 5. С телефона — открой myip.ru или fast.com
# IP должен быть exit-VPN-провайдера
```

---

## Версии агента и их вклад

| Версия | Что добавила |
|---|---|
| 0.2.13 | Первый self-bypass (SSH dport 22) |
| 0.2.14 | + agent-port + ESTABLISHED bypass |
| 0.2.15 | default-route БЕЗ `via` (P2P-aware) |
| 0.2.16 | RELATED,ESTABLISHED bypass |
| 0.2.17 | per-chain reconcile (раньше merge'или PREROUTING+OUTPUT в один dict) |
| 0.2.18 | MSS-clamp (OUTPUT — оказался не на той chain) |
| 0.2.19 | MSS-clamp в POSTROUTING (правильно) |
| 0.2.20 | Endpoint `/v1/containers` для UI |
| 0.2.21 | Auto-redeploy AWG-client'а в netns target'а (scope=container) |
| 0.2.22 | Sync ipset'ов в netns (per-netns kernel-state) |
| 0.2.23 | MSS-clamp без iface-фильтра (bipath) |
| 0.2.24 | delete_client сносит iface в правильной netns |
| 0.2.25 | self-bypass `addrtype --dst-type LOCAL` |
| 0.2.26 | (откачена) MARK в FORWARD chain — не работает: mark ставится после route lookup'а |
| 0.2.27 | revert на PREROUTING+OUTPUT |
| 0.2.28 | MARK только в PREROUTING (OUTPUT убрали — local TCP не должен идти через VPN) |

---

## Что точно не делать

- **Не маркировать в FORWARD** chain — mark ставится УЖЕ после route lookup'а,
  `ip rule fwmark` не вызывает reroute. Counter растёт, пакеты идут через
  initial out_iface. Проверено на 0.2.26.
- **Не маркировать local-originated** (OUTPUT) — socket-bind mismatch ломает
  reply'и для NEW TCP. Только `--interface` режим работает.
- **Не использовать catch-all без `addrtype LOCAL` bypass** — self-IP попадает
  в set, incoming handshake'и теряются.
- **Не запускать AWG-server в `--network bridge`** для scope=host — двойной
  NAT через docker. Либо `--network host`, либо scope=container.
