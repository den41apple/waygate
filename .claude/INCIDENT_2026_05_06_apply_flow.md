# Инцидент 2026-05-06: apply waygate-direction на yandex VM

**Хронологический лог проблем, попыток и результатов.**
Цель — чтобы будущие сессии не ходили по тем же кругам.

---

## TL;DR (что работает в итоге)

- ✅ **Direction "ВСЁ" применяется и маршрутизирует через eurohoster.**
  Phone подключается к `awg-test-srv` (UDP/52000) → forwarded трафик
  match'ится `match-set all-internet-v4` → `mark=0x3` → `ip rule fwmark 0x3
  lookup 102` → `default dev awg-eurohoster` → `MASQUERADE -o awg-eurohoster`
  → eurohoster server. На phone https://2ip.ru показывает eurohoster IP.

- ⚠️ **Скорость деградирует** (5 мин загрузка страницы) — MTU/MSS, см. open
  follow-up.

## Симптомы при которых наступили

1. Phone подключается к `awg-test-srv` ✓ handshake/transfer есть.
2. Через `amnezia-awg2` контейнер — handshake есть, transfer асимметричный,
   "почти не работает" (загрузил → переустановил через приложение → mismatch
   port + I-параметров → разобрались отдельно, см. `SESSION_2026_05_06.md`
   "Как чинили amnezia-awg2").
3. **Direction "ВСЁ" в waygate UI применился — но трафик с phone выходил
   через yandex IP, не eurohoster.** Это и было главным.

---

## Гипотезы и что показало (по порядку)

### Гипотеза 1: Race в `awg_clients.deploy_client` после `docker run -d`
**Ставка:** SESSION_2026_05_04 hipothesis. Подтвердилось логом агента
от 5 мая 09:00:13:
```
apply_rules: [host/v4] apply all-internet-v4: ip exited with 1:
  Cannot find device "awg-eurohoster"
```
**Fix:** `_wait_for_iface(timeout=10s)` после `docker run -d` (Phase A1
в плане `~/.claude/plans/fizzy-sprouting-rabbit.md`).
**Результат:** ✅ race закрылся, в логе после 0.2.30 "Cannot find device"
больше не появляется.

### Гипотеза 2: stale conntrack
**Ставка:** "пакеты от phone доходят, но возвратные не идут — conntrack
держит stale entries без NAT".
**Что попробовали:** `conntrack -F`, `conntrack -D -s 10.99.0.0/24`.
**Результат:** ❌ `0 flow entries deleted` — stale conntrack отпало.

### Гипотеза 3: Yandex Cloud Security Group
**Ставка:** UDP/52000 (наш test-srv) или UDP/44430 (новый amnezia-awg2)
не открыт.
**Что попробовали:** tcpdump на eth0 во время попытки phone.
**Результат:** ⚠️ packets приходят на eth0 → SG не блокирует. **Но** для
свежего amnezia-awg2 после переустановки port был не открыт → пользователь
открыл весь UDP-range через console.cloud.yandex.ru. Решение: при создании
VM сразу открывать UDP 1-65535 (это не стандартный security risk —
важные сервисы TCP).

### Гипотеза 4: rp_filter strict / Docker FORWARD drops
**Ставка:** SESSION_2026_05_04 hipotheses #2 и #3.
**Что попробовали:** `sysctl net.ipv4.conf.all.rp_filter` (=2 loose,
не виноват). `iptables -L DOCKER-USER` (пустой). `iptables -L DOCKER-FORWARD`
(accepts только amn0/docker0, но это **не** наш chain — packets от
awg-test-srv проходят раньше через FORWARD ACCEPT правила).
**Результат:** ❌ обе гипотезы отпали через LOG-target diagnostic
(см. ниже).

### Гипотеза 5 (КОРОНА): iptables-shadow-chain на Ubuntu 24.04 + Docker 28+
**Ставка:** через LOG-target в filter.FORWARD / mangle.POSTROUTING /
nat.POSTROUTING — увидели что counter в iptables-view растёт, а в реальной
nft chain (через `nft list`) не появляется. То есть `iptables -A` для
FORWARD-rule **попадает в shadow chain**, реальный hook её не видит.
**Fix #1 (workaround):** `nft insert rule ip filter FORWARD iifname/oifname
"awg-test-srv" counter accept` напрямую → packets пошли, transfer стал
симметричным, phone получил yandex IP.

### Подтверждение Gипотезы 5 для самого waygate-agent (NFT-1)
**Triggered:** после применения direction "ВСЁ" в UI — apply отчитался
`applied=1, errors=[]`, а в `nft list chain ip nat POSTROUTING` — НЕТ
MASQUERADE для `awg-eurohoster`. В `nft list chain ip mangle PREROUTING`
match-set ставит `mark 0x1` (leftover) вместо `mark 0x3` (наш direction).

**Что точно проверили (шаг 38):**
```bash
sudo iptables -t mangle -S PREROUTING   # видит наши правила (mark 0x3)
sudo nft list chain ip mangle PREROUTING # НЕ видит их
sudo update-alternatives --query iptables
# → Value: /usr/sbin/iptables-legacy   ← БИНГО
```
**Корень:** на этой VM `iptables` symlink → **iptables-legacy**. Agent
вызывал `iptables -A ...` → правила попадали в legacy `ip_tables` kernel
module, **который под Docker 28+ effectively отключён** (Docker управляет
nftables напрямую). Агент честно отчитывался "applied=1" — правила
действительно записаны в legacy table. Но они никем не обрабатываются.

**Fix #2:** `update-alternatives --set iptables /usr/sbin/iptables-nft`
+ то же для `ip6tables` + `systemctl restart waygate-agent` + force apply
через UI (toggle off → apply → on → apply).

**Результат:** ✅ ✅ direction "ВСЁ" применился, в `nft list chain ip
mangle PREROUTING` появилось `xt match "set" ... meta mark set 0x3`,
counter растёт (140+ packets), в `nat POSTROUTING` — `oifname
"awg-eurohoster" masquerade`. Phone выходит через eurohoster IP.

---

## Что НЕ делать в будущем

1. **Не гадать про conntrack stale** при mismatch'е mark/route counter'ов.
   Сначала: `nft list chain ip mangle PREROUTING` vs `iptables -t mangle
   -S PREROUTING` — если расходятся, это **shadow-chain bug** (NFT-1).

2. **Не диагностировать routing проблемы только через `iptables -L`.**
   На свежих Ubuntu (≥22.04) `iptables` может указывать на **legacy**
   alternative — view есть, реальные packets идут мимо. Всегда
   parallel-проверять `nft list ... | grep <iface>`.

3. **Не ходить по гипотезам RP_filter / DOCKER-USER / SG** до того, как
   подтвердить shadow-chain. На SESSION_2026_05_04 это съело целый день.

4. **Race fix в agent_v0.2.30 закрывает Cannot find device, но НЕ
   shadow-chain.** Если на свежей VM iptables=legacy, fix race не
   поможет — нужно ещё `update-alternatives --set iptables nft`.

---

## Команды-якори для быстрой диагностики

```bash
# 1. Проверка какой backend у iptables (КРИТИЧНО для NFT-1):
sudo update-alternatives --query iptables | grep Value
# Должно быть: /usr/sbin/iptables-nft. Если legacy — see fix ниже.

# 2. Сравнение iptables-view vs реальный nft:
sudo iptables -t mangle -S PREROUTING
sudo nft list chain ip mangle PREROUTING
# Если первое содержит match-set/MARK, а второе нет — shadow-chain.

# 3. Fix shadow-chain (одноразово на VM):
sudo update-alternatives --set iptables /usr/sbin/iptables-nft
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-nft
sudo systemctl restart waygate-agent
# Затем в UI: toggle off → apply → on → apply.

# 4. Если apply отчитывается ОК но routing не работает — проверить
#    что mark из mangle совпадает с mark из ip rule:
sudo nft list chain ip mangle PREROUTING | grep "mark set"
ip rule | grep fwmark
```

---

## Open follow-up'ы (для следующей сессии / отдельных пакетов)

### NFT-3 (HIGH). Provisioner должен ставить iptables=nft alternative
SSH-онбординг свежей VM (`backend/server/provisioner/`) **должен** делать
```bash
update-alternatives --set iptables /usr/sbin/iptables-nft
update-alternatives --set ip6tables /usr/sbin/ip6tables-nft
```
до установки агента. Иначе фикс race в 0.2.30 спасает только от Cannot
find device, но shadow-chain убивает apply на любой freshly-provisioned
машине с alternative=legacy. Yandex Cloud Ubuntu 24.04 image такой по
дефолту.

### NFT-4 (MEDIUM). Duplicate MASQ rule в nat POSTROUTING
После force apply увидели **две** одинаковые `oifname "awg-eurohoster"
counter packets 0 bytes 0 masquerade` строки в `ip nat POSTROUTING`.
Один — от старого apply (которого agent не очистил), второй — новый.
Reconcile-логика в `agent/routing.py::_apply_rules_in_scope_family`
неточно дедуплицирует MASQ-правила (вероятно ключ в `_read_state`
сравнивает по чему-то, отличающемуся между двумя проходами через legacy
vs nft). Не критично — counter обоих 0, packet-flow работает через
любое (одно сработает первым).

### MTU-1 (HIGH, прямо сейчас). MSS-clamp для awg-eurohoster
Phone через eurohoster открывает страницы за 5 минут. Большие TCP-pkt
фрагментируются на двойной AWG-обёртке (phone→awg-test-srv→awg-eurohoster
→ effective MTU ~1340 vs default 1500). Waygate ставит TCPMSS-clamp для
своих туннелей в `mangle FORWARD oifname "awg-X" tcp flags syn ... maxseg
set rt mtu` (см. шаг 42 в command.txt). Если оно в legacy после прошлого
apply — нужно force re-apply через UI чтобы оно переехало в nft, либо
руками `nft add rule ip mangle FORWARD oifname "awg-eurohoster" tcp
flags syn / syn,rst tcp option maxseg size set rt mtu`.

### MASQ-1 (closed). Конфликтующее MASQ rule из шага 11
Наше ручное `ip saddr 10.99.0.0/24 masquerade` (handle 30) перехватывало
трафик ДО того как packet уходил на awg-eurohoster через mark-routing →
src перезаписывался в eth0 IP, eurohoster дропал packets с unknown src.
Удаление rule + conntrack flush → packet flow восстановился. Также
найдены **3 одинаковых** `oifname "awg-eurohoster" masquerade` rules
(дубликаты от idempotent-сбоя в `_read_state`) — оставлен один.

### Slow-page-loads (closed, не bug). Архитектурный overhead двойного AWG
Через `phone → awg-test-srv → awg-eurohoster → eurohoster server` RTT
до 1.1.1.1 = ~50ms (vs прямой eth0 = 14ms). Curl-timing для https://yandex.ru:
- dns=5ms tcp_connect=98ms tls=200ms ttfb=306ms total=306ms (через VPN)
- dns=1ms tcp_connect=7ms tls=44ms ttfb=84ms total=84ms (eth0 direct)

Overhead +220ms на каждый request. YouTube не страдает (один долгий
TCP-stream). Сайт с 30+ sub-resources страдает (накопительно +10-15 сек
HTTP/1.1 или +2-3 сек HTTP/2). **Не bug** — норма для двойного VPN.

Решение для prod: phone должен подключаться **напрямую к waygate-managed
AWG-server** (без посреднического test-srv), это даст +50ms overhead
вместо +220ms. Test-srv был только для нашей отладки.

### NFT-2 (LOW). `scripts/setup-test-awg-server.sh` на nft
Скрипт пишет PostUp/PostDown через `iptables` — на VM с iptables=legacy
не работало изначально. Сейчас после переключения alternative — работает,
но для voids на чистой VM без переключения по-прежнему сломан. План в
backlog.

---

## Контекст состояния VM на момент написания (2026-05-06)

- **agent версия**: 0.2.30 (с _wait_for_iface + честный counter +
  last-apply.json + auto-reapply в healthcheck — Phase A/B/C плана
  `fizzy-sprouting-rabbit.md`)
- **iptables alternative**: nft (переключено вручную в шаге 39, нужно
  закрепить в provisioner — NFT-3)
- **направления в waygate**:
  - "RU" via awg-firstbyte → 10.8.1.0 (paused)
  - "NL" via awg-eurohoster → 10.8.1.1 (paused)
  - "ВСЁ" via awg-eurohoster → 10.8.1.1 (active, **работает**)
- **awg-test-srv** (UDP/52000) на host'е — phone подключён, transfer
  растёт, форвардит через direction "ВСЁ"
- **amnezia-awg2** (UDP/44430) — переустановлен через приложение, после
  правок (port mismatch + I-params commented) handshake проходит, но
  пользователь использует amnezia-awg2 параллельно для других клиентов
- **MTU/MSS** — пока не settled, скорости 5-минутной загрузки
