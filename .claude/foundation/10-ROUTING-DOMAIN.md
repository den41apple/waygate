# 10 — Доменный reference: netfilter-роутинг (сжатый)

Домен нового проекта тот же, но акцент сместился на деплой. Этот документ — **сжатый reference**,
чтобы не потерять доменное знание. **Полный источник истины — `ROUTING_ARCHITECTURE.md` в доноре**
(читать его при любых routing-вопросах). Донор-код: `backend/agent/routing.py`.

## Что делает система (1 абзац)

Клиент (телефон/мак) подключается к target-хосту через VPN-конфиг. На хосте AWG-server-контейнер
расшифровывает трафик; он попадает в host netns как forwarded TCP/UDP. Агент **ставит mangle MARK**
по match-set правилам → `ip rule fwmark X table Y` → `table Y: default dev awg-<client>` → пакет
уходит в Waygate-managed AWG-client-контейнер → наружу с нужным гео-IP.

## Главные инварианты (нарушать = часы дебага)

- **MARK ставится ТОЛЬКО в `PREROUTING`** (`_MARK_CHAINS = ("PREROUTING",)`).
  - **Не FORWARD:** mark там ставится **после** route lookup → `ip rule fwmark` не вызывает reroute
    (counter растёт, пакет идёт по initial route). Проверено и откачено (0.2.26).
  - **Не OUTPUT:** local-originated TCP ломается socket-bind mismatch'ем (curl bind'ится к eth0_IP
    до OUTPUT mangle, mark переключает на awg-X, reply ищет original socket → RST/timeout).
- **Self-bypass обязателен** (в начале PREROUTING), иначе catch-all направление отрубит свой же
  SSH/agent: `addrtype --dst-type LOCAL → RETURN` (incoming на свой IP) + `ESTABLISHED → RETURN`
  (reply'и не маркируем — conntrack ведёт). С 0.2.32 это всего 2 правила (addrtype покрывает SSH/
  agent/handshake одной строкой).
- **MASQUERADE на каждом awg-iface** (`nat POSTROUTING -o awg-<client> MASQUERADE`) — иначе src
  остаётся приватным (10.x), VPN-провайдер дропает как spoofed.
- **MSS-clamp в POSTROUTING без `-o` фильтра** (`--clamp-mss-to-pmtu`) — ядро само подберёт MSS по
  PMTU выходного iface'а. В OUTPUT не работает (пакет ещё на eth0 до reroute).
- **default-route без `via <gw>`** — AWG-туннели POINTOPOINT, ядро само инкапсулирует, ARP не нужен.
- **Dual-family ipset:** логический `<name>` → физические `<name>-v4` (inet) и `<name>-v6` (inet6).
  v6-стек скипать на iface без global IPv6 (иначе silent drop).
- **MTU=1280** для AWG-iface при двойном туннеле (1420 не хватает, большие TCP фрагментируются).

## Два scope'а

- **scope=host** — AWG-server в `--network host`; mangle/route в host netns. **Нельзя `--network
  bridge`** для AWG-server (двойной NAT через docker-NAT + наш MASQUERADE → conntrack ломается → 0 b/s).
- **scope=container** — AWG-client переключается в netns target'а (`--network container:<имя>`),
  правила применяются через `nsenter -t <pid> -n`. Один AWG-client = одна netns одновременно.

## Что точно НЕ делать

- Не маркировать в FORWARD/OUTPUT (см. инварианты).
- Не использовать catch-all без `addrtype LOCAL` bypass'а.
- Не запускать AWG-server в bridge для scope=host.
- **Не патчить routing/iptables инкрементально** — только полный idempotent reconcile (см. `04`).
- **Не добавлять `nft add rule` в chain, управляемую iptables-nft compat** — ломает compat-флаг всей
  таблицы, следующий `iptables -S/-F` агента упадёт с «table incompatible». Ad-hoc rule ставить либо
  тем же `iptables`, что и агент, либо в **отдельную nft-таблицу**.

## Главная системная граблина (вынесена в `11`)

На Ubuntu 24.04 + Docker 28+ `iptables` может указывать на **legacy** alternative → правила уходят в
отключённый legacy-модуль, агент честно пишет «applied=1», но в ядре ничего нет (**shadow-chain**).
Лечится `update-alternatives --set iptables iptables-nft` (провижионер делает это, см. `05`).
Диагностика — сравнить `iptables -t mangle -S` vs `nft list chain ip mangle PREROUTING`.
