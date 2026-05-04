"""SSH-based uninstall — снести waygate-agent и все его следы с target-сервера.

Обратная операция к `provisioner.service.run_provision`. Используется когда
оператор хочет полностью удалить waygate с сервера: останавливает сервис,
сносит venv/конфиги/данные, чистит iptables/ipset/route, удаляет AWG-client-
контейнеры. Системные пакеты (dnsmasq, iptables, amneziawg-dkms) НЕ удаляем —
они могут быть нужны другим сервисам.
"""

from server.provisioner.ssh import SshSession
from server.provisioner.steps_types import ProgressEmitter


async def uninstall_agent(*, ssh: SshSession, emit: ProgressEmitter) -> None:
    """Полный cleanup target-сервера от waygate-agent'а.

    Идемпотентно: повторный запуск на уже-чистом сервере просто вернётся без
    ошибок (все команды с `check=False` или `|| true`). Не падает если нечего
    удалять.
    """
    await ssh.ensure_root_or_sudo()

    await emit("Останавливаю waygate-agent.service…")
    await ssh.run(command="systemctl stop waygate-agent 2>/dev/null || true", check=False)
    await ssh.run(command="systemctl disable waygate-agent 2>/dev/null || true", check=False)

    await emit("Удаляю waygate AWG-client-контейнеры…")
    await ssh.run(
        command=("docker ps -a --filter label=io.waygate.role=client --format '{{.Names}}' | xargs -r docker rm -f"),
        check=False,
    )

    await emit("Сношу AWG-iface'ы (awg-*)…")
    await ssh.run(
        command="ip -o link show | awk -F': ' '/awg-/{print $2}' | xargs -r -I{} ip link delete {}",
        check=False,
    )

    await emit("Чистка iptables mangle (PREROUTING/OUTPUT/FORWARD/POSTROUTING)…")
    await ssh.run(command="iptables -t mangle -F PREROUTING", check=False)
    await ssh.run(command="iptables -t mangle -F OUTPUT", check=False)
    await ssh.run(command="iptables -t mangle -F FORWARD", check=False)
    await ssh.run(command="iptables -t mangle -F POSTROUTING", check=False)
    await ssh.run(command="ip6tables -t mangle -F PREROUTING", check=False)
    await ssh.run(command="ip6tables -t mangle -F OUTPUT", check=False)
    await ssh.run(command="ip6tables -t mangle -F FORWARD", check=False)
    await ssh.run(command="ip6tables -t mangle -F POSTROUTING", check=False)

    await emit("Чистка iptables nat POSTROUTING (только awg-* MASQUERADE)…")
    # Удаляем только наши awg-* MASQUERADE правила, не трогаем Docker и прочие.
    await ssh.run(
        command=(
            "iptables -t nat -S POSTROUTING | grep -E '\\-A POSTROUTING.*awg-' | "
            "sed 's/-A /-D /' | while read rule; do iptables -t nat $rule 2>/dev/null || true; done"
        ),
        check=False,
    )

    await emit("Чистка ip rule fwmark и custom-таблиц…")
    await ssh.run(
        command=(
            "ip rule show | awk '/fwmark/{print $1}' | sed 's/://' | "
            "while read prio; do ip rule del prio $prio 2>/dev/null || true; done"
        ),
        check=False,
    )
    # Custom routing tables 100-200 (waygate использует 100+)
    await ssh.run(
        command="for t in $(seq 100 200); do ip route flush table $t 2>/dev/null; done",
        check=False,
    )

    await emit("Удаляю waygate-managed ipset'ы…")
    await ssh.run(
        command=("ipset list -name 2>/dev/null | grep -E '^(geoip-|dns-|.*-v[46]$)' | xargs -r -I{} ipset destroy {}"),
        check=False,
    )

    await emit("Удаляю файлы waygate (venv/config/data)…")
    await ssh.run(command="rm -rf /opt/waygate-agent", check=False)
    await ssh.run(command="rm -rf /etc/waygate", check=False)
    await ssh.run(command="rm -rf /var/lib/waygate-agent", check=False)
    await ssh.run(command="rm -rf /etc/amnezia", check=False)

    await emit("Удаляю dnsmasq waygate-конфиг…")
    await ssh.run(command="rm -f /etc/dnsmasq.d/waygate.conf", check=False)
    await ssh.run(command="systemctl restart dnsmasq 2>/dev/null || true", check=False)

    await emit("Удаляю systemd-юнит и daemon-reload…")
    await ssh.run(command="rm -f /etc/systemd/system/waygate-agent.service", check=False)
    await ssh.run(command="systemctl daemon-reload", check=False)

    await emit("Готово. Waygate-agent полностью удалён с сервера.")
