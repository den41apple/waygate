"""SSH-based agent update — server открывает SSH, скачивает wheel, swap'ает venv.

Альтернатива self-update'у (`agent/updater.py`), который упирается в
`ProtectSystem=strict` systemd-юнита: agent-процесс не может писать в `/opt/`.
SSH-сессия = независимый root-shell, namespace-ограничения unit'а к ней не
применяются.

Используется через `_run_ssh_update()` в `server/api/servers.py` или
`server/tasks/update_runner.py` — в зависимости от того, есть ли у Server
сохранённые SSH-креды.
"""

from server.provisioner.ssh import SshSession
from server.provisioner.steps import ProgressEmitter


async def update_agent_via_ssh(
    *,
    ssh: SshSession,
    wheel_url: str,
    version: str,
    emit: ProgressEmitter,
) -> None:
    """Atomic-swap-update: новый venv в `.new`, mv'ом меняем местами, restart.

    Вся последовательность идемпотентна — повторный запуск с теми же параметрами
    не сломает что-то (рекомендуется `rm -rf` следов прошлых попыток в начале).
    """
    await emit("Скачиваю wheel...")
    await ssh.run(
        command=f"curl -fsSL '{wheel_url}' -o /tmp/waygate_agent.whl",
    )

    await emit("Создаю свежий venv в /opt/waygate-agent.new...")
    # rm -rf — без check, чтобы первый запуск (когда .new/.bak не существуют) не падал.
    await ssh.run(command="rm -rf /opt/waygate-agent.new /opt/waygate-agent.bak", check=False)
    await ssh.run(command="python3 -m venv /opt/waygate-agent.new")

    await emit("Устанавливаю wheel...")
    await ssh.run(command="/opt/waygate-agent.new/bin/pip install --upgrade --quiet pip")
    await ssh.run(command="/opt/waygate-agent.new/bin/pip install --quiet /tmp/waygate_agent.whl")

    await emit("Atomic swap...")
    await ssh.run(command="mv /opt/waygate-agent /opt/waygate-agent.bak")
    await ssh.run(command="mv /opt/waygate-agent.new /opt/waygate-agent")

    await emit("Restart waygate-agent.service...")
    await ssh.run(command="systemctl restart waygate-agent")

    await emit("Cleanup...")
    await ssh.run(
        command="rm -rf /opt/waygate-agent.bak /tmp/waygate_agent.whl",
        check=False,
    )

    await emit(f"Готово. Установлена версия {version}.")
