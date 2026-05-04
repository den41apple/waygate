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

echo "=== Запись server config $CONFIG_DIR/$IFACE.conf ==="
cat > "$CONFIG_DIR/$IFACE.conf" <<EOF
[Interface]
PrivateKey = $SERVER_PRIV
Address = $SERVER_IP/24
ListenPort = $PORT
Jc = 5
Jmin = 10
Jmax = 50
S1 = 44
S2 = 66
S3 = 39
S4 = 12
H1 = 665634046
H2 = 1886476213
H3 = 2089831652
H4 = 2147473214
PostUp = iptables -t nat -A POSTROUTING -s $SERVER_IP/24 ! -d $SERVER_IP/24 -j MASQUERADE; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; sysctl -w net.ipv4.ip_forward=1
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
Jc = 5
Jmin = 10
Jmax = 50
S1 = 44
S2 = 66
S3 = 39
S4 = 12
H1 = 665634046
H2 = 1886476213
H3 = 2089831652
H4 = 2147473214

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
