#!/usr/bin/env bash
# Поднимает второй AmneziaWG-server рядом с amnezia-awg2 — в host netns,
# чтобы избежать двойного NAT через docker bridge. Используется для теста
# скорости forwarded-трафика через scope=host VPN-direction в waygate.
#
# Запускать на target VM (например yandex_VM):
#   curl -fsSL https://raw.githubusercontent.com/den41apple/waygate/master/scripts/setup-test-awg-server.sh -o /tmp/awg-test.sh
#   sudo bash /tmp/awg-test.sh
#
# Идемпотентно: повторный запуск сгенерит новый keypair и пересоздаст iface.
#
# Требования: amneziawg-tools >= 1.0.20240130 (поддержка S3/S4, I2-I5,
# H1-H4 как range'ов). Проверить: `awg --version`. Если старая —
# `apt-get update && apt-get install -y amneziawg amneziawg-tools` из
# `ppa:amnezia/ppa`.

set -euo pipefail

# === Параметры (можно переопределить через env: PORT=51999 sudo bash ...) ===
PORT="${PORT:-52000}"
SERVER_IP="${SERVER_IP:-10.99.0.1}"
CLIENT_IP="${CLIENT_IP:-10.99.0.2}"
IFACE="${IFACE:-awg-test-srv}"
CONFIG_DIR="${CONFIG_DIR:-/etc/amnezia-test-server}"

PUBLIC_IP=$(curl -4 -s --max-time 3 ifconfig.me 2>/dev/null \
  || curl -4 -s --max-time 3 ipinfo.io/ip 2>/dev/null \
  || ip -4 addr show eth0 | awk '/inet / {sub(/\/.*/, "", $2); print $2; exit}')

mkdir -p "$CONFIG_DIR"

# Снести предыдущий iface если был — для идемпотентности.
awg-quick down "$CONFIG_DIR/$IFACE.conf" 2>/dev/null || true
ip link delete "$IFACE" 2>/dev/null || true

echo "=== Генерация ключей ==="
SERVER_PRIV=$(awg genkey)
SERVER_PUB=$(echo "$SERVER_PRIV" | awg pubkey)
CLIENT_PRIV=$(awg genkey)
CLIENT_PUB=$(echo "$CLIENT_PRIV" | awg pubkey)
PSK=$(awg genpsk)

# === AWG 2.0 obfuscation параметры (из AWG_PARAMS.txt — рабочая конфа) ===
# Должны совпадать у сервера и клиента. Требуют свежего amneziawg-tools
# (поддержка S3/S4, I2-I5, H1-H4 как range'ов появилась в 2.0).
AWG_JC="5"
AWG_JMIN="10"
AWG_JMAX="50"
AWG_S1="44"
AWG_S2="66"
AWG_S3="39"
AWG_S4="12"
AWG_H1="665634046-1180630982"
AWG_H2="1886476213-1909461507"
AWG_H3="2089831652-2130352892"
AWG_H4="2147473214-2147483099"
AWG_I1="<b 0x5245474953544552207369703a676f6f676c652e636f6d205349502f322e300d0a5669613a205349502f322e302f554450203139322e3136382e35322e3233353a353036303b6272616e63683d7a39684734624b3936353335393533613338353834643930353666393938390d0a4d61782d466f7277617264733a2037300d0a546f3a203c7369703a7573657240676f6f676c652e636f6d3e0d0a46726f6d3a203c7369703a7573657240676f6f676c652e636f6d3e3b7461673d636132376530343865646339306532610d0a43616c6c2d49443a2035643833393837613730353961393631656263646463336262643761616335380d0a435365713a20312052454749535445520d0a436f6e746163743a203c7369703a75736572403139322e3136382e3233352e323a353036303e0d0a557365722d4167656e743a205a657068797220322e302e300d0a457870697265733a20353037340d0a436f6e74656e742d4c656e6774683a20300d0a0d0a>"
AWG_I2="<b 0x160303005f0100005b030367827544015cf75dab82bb2a677440f556dfcb02bbc9e4522961673f55aa8f680727ada1a040153f0002c02b010000290000000e00000c000009676d61696c2e636f6d000b000403000102000a000a0008032ad7194ea3d2b1>"
AWG_I3="<b 0x160303002e0200002a0303557cd0c3430e67a7a14e41e329ca11179bd5e05fdd1460bca35412a2ff2ea63602d718c02c000000>"
AWG_I4="<b 0x10000080b7c5dfc33b86339958f4802cab9fb0f6e0627f41b9c5aba6ec1eaec48c2bf676b5aab20faf85776e8e2daf5e387cdc1870dc3a1f89ead7d863e0b2b04998de231d033d12c105d1c76f53ef978a49a5204f683e173316d10ead115fa1e6713918276c8e244ec25b267e156f97b956391b12db60e420406ab90aa48d6788b5594014030300010116030300340cf47387a308fb1c02e2611e0380ef9d4860f032bb8fc26dcb22c64e59288f07d7e85e0371e8ded4178a99e5ae7bf704f361cd31>"
AWG_I5="<b 0x170303015648454144202f20485454502f312e310d0a486f73743a20676d61696c2e636f6d0d0a557365722d4167656e743a204d6f7a696c6c612f352e30202857696e646f7773204e542031302e303b2057696e36343b2078363429204170706c655765624b69742f3533372e333620284b48544d4c2c206c696b65204765636b6f29204368726f6d652f39312e302e343437322e313234205361666172692f3533372e33360d0a4163636570743a20746578742f68746d6c2c6170706c69636174696f6e2f7868746d6c2b786d6c2c6170706c69636174696f6e2f786d6c3b713d302e392c696d6167652f776562702c2a2f2a3b713d302e380d0a4163636570742d4c616e67756167653a20656e2d55532c656e3b713d302e350d0a4163636570742d456e636f64696e673a20677a69702c206465666c6174652c2062720d0a436f6e6e656374696f6e3a206b6565702d616c6976650d0a0d0a>"

echo "=== Запись server config $CONFIG_DIR/$IFACE.conf ==="
cat > "$CONFIG_DIR/$IFACE.conf" <<EOF
[Interface]
PrivateKey = $SERVER_PRIV
Address = $SERVER_IP/24
ListenPort = $PORT
Jc = $AWG_JC
Jmin = $AWG_JMIN
Jmax = $AWG_JMAX
S1 = $AWG_S1
S2 = $AWG_S2
S3 = $AWG_S3
S4 = $AWG_S4
H1 = $AWG_H1
H2 = $AWG_H2
H3 = $AWG_H3
H4 = $AWG_H4
I1 = $AWG_I1
I2 = $AWG_I2
I3 = $AWG_I3
I4 = $AWG_I4
I5 = $AWG_I5
PostUp = iptables -t nat -A POSTROUTING -s $SERVER_IP/24 ! -d $SERVER_IP/24 -j MASQUERADE; iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -I FORWARD 1 -o %i -j ACCEPT; sysctl -w net.ipv4.ip_forward=1
PostDown = iptables -t nat -D POSTROUTING -s $SERVER_IP/24 ! -d $SERVER_IP/24 -j MASQUERADE; iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT

[Peer]
PublicKey = $CLIENT_PUB
PresharedKey = $PSK
AllowedIPs = $CLIENT_IP/32
EOF
chmod 600 "$CONFIG_DIR/$IFACE.conf"

echo "=== Поднимаем iface через awg-quick (host netns, kernel module) ==="
awg-quick up "$CONFIG_DIR/$IFACE.conf"

echo ""
echo "=== Verify ==="
ip link show "$IFACE" | head -2
awg show "$IFACE" | head -10
echo ""
echo "Listening UDP-порт:"
ss -lunp | grep ":$PORT" || echo "(не вижу listening через ss, но iface может работать)"

echo ""
echo "================================================================="
echo "CLIENT CONFIG — скопируй и импортируй в AmneziaVPN на телефоне"
echo "================================================================="
cat <<EOF

[Interface]
PrivateKey = $CLIENT_PRIV
Address = $CLIENT_IP/32
DNS = 1.1.1.1, 1.0.0.1
Jc = $AWG_JC
Jmin = $AWG_JMIN
Jmax = $AWG_JMAX
S1 = $AWG_S1
S2 = $AWG_S2
S3 = $AWG_S3
S4 = $AWG_S4
H1 = $AWG_H1
H2 = $AWG_H2
H3 = $AWG_H3
H4 = $AWG_H4
I1 = $AWG_I1
I2 = $AWG_I2
I3 = $AWG_I3
I4 = $AWG_I4
I5 = $AWG_I5

[Peer]
PublicKey = $SERVER_PUB
PresharedKey = $PSK
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = $PUBLIC_IP:$PORT
PersistentKeepalive = 25
EOF

echo ""
echo "================================================================="
echo "Чтобы остановить test-сервер позже:"
echo "  sudo awg-quick down $CONFIG_DIR/$IFACE.conf"
echo "================================================================="
