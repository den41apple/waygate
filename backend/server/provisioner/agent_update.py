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
from server.provisioner.steps_types import ProgressEmitter
from server.provisioner.wheel_fetch import fetch_wheel_to_target


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
    # PEP 427: pip требует чтобы имя файла было `{name}-{version}-{python}-{abi}-{platform}.whl`.
    # Иначе он падает с "Invalid wheel filename (wrong number of parts)".
    wheel_path = f"/tmp/waygate_agent-{version}-py3-none-any.whl"

    await emit("Скачиваю wheel...")
    await fetch_wheel_to_target(ssh=ssh, wheel_url=wheel_url, wheel_path=wheel_path, emit=emit)

    await emit("Создаю свежий venv в /opt/waygate-agent.new...")
    # rm -rf — без check, чтобы первый запуск (когда .new/.bak не существуют) не падал.
    await ssh.run(command="rm -rf /opt/waygate-agent.new /opt/waygate-agent.bak", check=False)
    await ssh.run(command="python3 -m venv /opt/waygate-agent.new")

    await emit("Устанавливаю wheel...")
    await ssh.run(command="/opt/waygate-agent.new/bin/pip install --upgrade --quiet pip")
    await ssh.run(command=f"/opt/waygate-agent.new/bin/pip install --quiet '{wheel_path}'")

    await emit("Релокация shebang'ов под финальный путь...")
    # pip генерирует bin-wrapper'ы (granian, pip, fastapi и т.п.) с абсолютным
    # shebang'ом `#!/opt/waygate-agent.new/bin/python3`. После `mv .new → live`
    # этот путь становится несуществующим, и systemd не может exec'нуть granian
    # ("Failed to execute /opt/waygate-agent/bin/granian: No such file"). Перед
    # swap'ом замещаем `.new` на финальный путь во всех текстовых файлах venv'а.
    # `grep -rl ... | xargs --no-run-if-empty` пропускает binary и не падает на
    # пустом списке. pyvenv.cfg тоже ссылается на старый путь — правим отдельно.
    await ssh.run(
        command=(
            "grep -rIl 'opt/waygate-agent.new' /opt/waygate-agent.new/bin/ | "
            "xargs --no-run-if-empty sed -i "
            "'s|/opt/waygate-agent.new|/opt/waygate-agent|g'"
        ),
    )
    await ssh.run(
        command=("sed -i 's|/opt/waygate-agent.new|/opt/waygate-agent|g' /opt/waygate-agent.new/pyvenv.cfg"),
    )

    await emit("Atomic swap...")
    await ssh.run(command="mv /opt/waygate-agent /opt/waygate-agent.bak")
    await ssh.run(command="mv /opt/waygate-agent.new /opt/waygate-agent")

    await emit("Restart waygate-agent.service...")
    await ssh.run(command="systemctl restart waygate-agent")

    await emit("Cleanup...")
    await ssh.run(
        command=f"rm -rf /opt/waygate-agent.bak '{wheel_path}'",
        check=False,
    )

    await emit(f"Готово. Установлена версия {version}.")
