from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncssh


class SshError(RuntimeError):
    """Ошибка SSH-соединения или выполнения команды."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _decode(value: str | bytes | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace")


class SshSession:
    """Тонкая обёртка над asyncssh.SSHClientConnection — упрощает мокинг в тестах.

    Поддерживает sudo-режим: если SSH-юзер не root, провижнер вызывает
    `enable_sudo()` после проверки `sudo -n true`, и все последующие команды
    оборачиваются в `sudo -n sh -c '<cmd>'`. Обёртка через `sh -c` нужна потому
    что shell-redirect/heredoc/pipe нужно интерпретировать УЖЕ под sudo —
    иначе `sudo cat > /etc/...` сделает редирект из под текущего юзера.
    """

    def __init__(self, *, connection: asyncssh.SSHClientConnection) -> None:
        self._connection = connection
        self._sudo_prefix = ""

    def enable_sudo(self) -> None:
        self._sudo_prefix = "sudo -n "

    def _wrap(self, command: str) -> str:
        if not self._sudo_prefix:
            return command
        # POSIX-trick для безопасного литерального закавычивания: каждый `'`
        # внутри строки превращается в `'\''` (close-quote, escaped-quote, open-quote).
        escaped = command.replace("'", "'\\''")
        return f"{self._sudo_prefix}sh -c '{escaped}'"

    async def run(self, *, command: str, check: bool = True) -> CommandResult:
        wrapped = self._wrap(command)
        result = await self._connection.run(wrapped, check=False)
        stdout = _decode(result.stdout)
        stderr = _decode(result.stderr)
        returncode = result.returncode if result.returncode is not None else -1
        if check and returncode != 0:
            raise SshError(f"command exited with {returncode}: {command} | stderr: {stderr.strip()}")
        return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)

    async def write_file(self, *, path: str, content: str, mode: str = "0644") -> None:
        # Заливаем через cat-heredoc — пригодно для конфигов и unit-файлов любого размера.
        # Используем строгий heredoc (одинарные кавычки), чтобы шелл не интерпретировал $.
        escaped_path = path.replace("'", "'\\''")
        marker = "WAYGATE_EOF"
        await self.run(command=f"mkdir -p $(dirname '{escaped_path}')")
        full = f"cat > '{escaped_path}' <<'{marker}'\n{content}\n{marker}"
        await self.run(command=full)
        await self.run(command=f"chmod {mode} '{escaped_path}'")

    async def ensure_root_or_sudo(self) -> None:
        """Если SSH-юзер не root — проверяет NOPASSWD-sudo и включает sudo-режим.

        Идемпотентно. Вызывается из всех мест, где нужен root: онбординг (через
        `verify_os`) и agent-update (через `update_agent_via_ssh`). Без этого
        `python3 -m venv /opt/...`, `apt-get`, `chattr` и т.п. валятся с
        Permission denied на не-root юзерах.
        """
        if self._sudo_prefix:
            return
        uid_result = await self.run(command="id -u")
        if uid_result.stdout.strip() == "0":
            return
        sudo_check = await self.run(command="sudo -n true", check=False)
        if sudo_check.returncode != 0:
            raise SshError(
                "SSH-юзер не root, и NOPASSWD-sudo не настроен. Варианты: "
                "1) подключайся как root@<host>; "
                "2) настрой sudoers на target-хосте — "
                "echo '$USER ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/waygate-nopasswd "
                "&& sudo chmod 440 /etc/sudoers.d/waygate-nopasswd",
            )
        self.enable_sudo()

    async def upload_bytes(self, *, path: str, content: bytes) -> None:
        """Заливает бинарные данные на target через SFTP.

        Используется для wheel'ов и других бинарников, когда `curl` на target
        не может достучаться до источника (RU-сервера + GitHub: блокировки,
        DNS-резолв через dnsmasq может лежать). Control-plane качает у себя,
        этот метод заливает на target по уже установленной SSH-сессии.

        Запись через SFTP идёт **под именем SSH-юзера**, не под root'ом, даже
        если включен sudo-режим. Для путей в /tmp/ это норма; для system-paths
        нужно сначала залить в /tmp, потом `mv` через `run()` (тогда mv пойдёт
        под sudo).
        """
        async with (
            self._connection.start_sftp_client() as sftp,
            sftp.open(path, "wb") as remote_file,
        ):
            await remote_file.write(content)


@asynccontextmanager
async def ssh_connect(
    *,
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    private_key: str | None = None,
) -> AsyncIterator[SshSession]:
    """Открывает SSH-соединение. Аутентификация по паролю ИЛИ по private_key (PEM).

    known_hosts=None — отключает проверку host key (онбординг сразу после deploy,
    мы ещё не знаем fingerprint). В production-сетапе хороший тон — добавить host
    в known_hosts вручную и повторно подключаться с проверкой.
    """
    connect_kwargs: dict[str, object] = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,
    }
    if password:
        connect_kwargs["password"] = password
    elif private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    else:
        raise SshError("Нужен password или private_key")

    try:
        async with asyncssh.connect(**connect_kwargs) as connection:
            yield SshSession(connection=connection)
    except (asyncssh.Error, OSError) as exc:
        raise SshError(f"SSH-подключение к {host}:{port} не удалось: {exc}") from exc
