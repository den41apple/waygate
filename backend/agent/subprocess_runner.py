import asyncio


class CommandError(RuntimeError):
    """Команда завершилась с ненулевым кодом."""

    def __init__(self, *, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{command[0]} exited with {returncode}: {stderr.strip()}")


async def run_command(
    command: list[str],
    *,
    stdin: bytes | None = None,
    check: bool = True,
) -> str:
    """Запускает команду и возвращает stdout. Не блокирует event loop.

    При check=True кидает CommandError на ненулевом returncode.
    При check=False возвращает stdout даже при ошибке (нужно для idempotent-проверок).
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate(stdin)
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if check and process.returncode != 0:
        raise CommandError(command=command, returncode=process.returncode or -1, stderr=stderr)
    return stdout
