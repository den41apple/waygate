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
    """Тонкая обёртка над asyncssh.SSHClientConnection — упрощает мокинг в тестах."""

    def __init__(self, *, connection: asyncssh.SSHClientConnection) -> None:
        self._connection = connection

    async def run(self, *, command: str, check: bool = True) -> CommandResult:
        result = await self._connection.run(command, check=False)
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
