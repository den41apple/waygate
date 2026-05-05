"""Integration-тесты против реального sshd-контейнера для `provisioner.ssh`.

Скип если docker daemon недоступен. Поднимает Ubuntu 22.04 с sshd,
password-auth и тестовым root-юзером. Проверяет что наш `SshSession`
работает с реальным OpenSSH'ом — асинк-соединение, run() с
returncode'ами, write_file через heredoc, ensure_root_or_sudo.

Регрессия #C4: раньше unit-тесты в `test_provisioner.py` мокали
`SshSession.run`, и любые баги в quoting'е (write_file через heredoc,
sudo-wrap'е) ловились только на проде.

По умолчанию выключены через `addopts = -m 'not integration'`. Запуск:
`uv run pytest -m integration server/tests/test_ssh_integration.py`.
"""

import contextlib
import time
import uuid
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.integration


_SSHD_PASSWORD = "waygate-test-pwd-do-not-use-in-prod"
_SSHD_PORT_HOST = 22222
_SSHD_DOCKERFILE = """
FROM ubuntu:22.04

RUN apt-get update -qq && \\
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \\
        openssh-server sudo && \\
    rm -rf /var/lib/apt/lists/*

# sshd нужен writable /run/sshd для pid'а.
RUN mkdir -p /run/sshd && \\
    echo 'root:__PASSWORD__' | chpasswd && \\
    sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \\
    sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


def _docker_available() -> bool:
    try:
        import docker
    except ImportError:
        return False
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="module")
def sshd_container() -> Iterator[tuple[str, int, str]]:
    """Поднимает sshd-контейнер, отдаёт (host, port, password).

    Module-scoped — экономим build/run time. Тесты должны быть idempotent
    относительно файловой системы целевого контейнера (создавать файлы под
    /tmp с уникальными именами).
    """
    if not _docker_available():
        pytest.skip("docker daemon недоступен — пропускаем integration-тесты")

    import tarfile
    from io import BytesIO

    import docker

    client = docker.from_env()
    tag = "waygate-sshd:integration-test"

    # Inline-build: docker-py принимает fileobj=Dockerfile-content. Используем
    # tar-архив для полноценного build context (тут только Dockerfile нужен).
    dockerfile_content = _SSHD_DOCKERFILE.replace("__PASSWORD__", _SSHD_PASSWORD).encode()
    tar_buf = BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_content)
        tar.addfile(info, BytesIO(dockerfile_content))
    tar_buf.seek(0)

    # Build (cached на second run).
    client.images.build(fileobj=tar_buf, custom_context=True, tag=tag, rm=True)

    name = f"waygate-sshd-it-{uuid.uuid4().hex[:8]}"
    container = client.containers.run(
        tag,
        detach=True,
        name=name,
        ports={"22/tcp": _SSHD_PORT_HOST},
        auto_remove=False,
    )

    # Ждём пока sshd начнёт принимать TCP-коннекты.
    import socket

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", _SSHD_PORT_HOST), timeout=1.0):
                break
        except OSError:
            time.sleep(0.3)
    else:
        try:
            logs = container.logs(tail=40).decode(errors="replace")
            print(f"\n--- sshd container logs ---\n{logs}\n--- /logs ---", flush=True)
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)
        raise RuntimeError("sshd не открыл порт за 15s")

    try:
        yield ("127.0.0.1", _SSHD_PORT_HOST, _SSHD_PASSWORD)
    finally:
        with contextlib.suppress(Exception):
            container.kill()
        with contextlib.suppress(Exception):
            container.remove(force=True)


async def test_ssh_connect_runs_command(sshd_container: tuple[str, int, str]) -> None:
    """Smoke: SshSession.run() против реального sshd работает."""
    from server.provisioner.ssh import ssh_connect

    host, port, password = sshd_container
    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        result = await ssh.run(command="echo hello && id -u")
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert "0" in result.stdout  # uid 0 (root)


async def test_ssh_run_propagates_nonzero_exit_with_check(sshd_container: tuple[str, int, str]) -> None:
    """check=True + non-zero exit → SshError."""
    from server.provisioner.ssh import SshError, ssh_connect

    host, port, password = sshd_container
    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        with pytest.raises(SshError):
            await ssh.run(command="exit 7")


async def test_ssh_run_no_check_returns_returncode(sshd_container: tuple[str, int, str]) -> None:
    """check=False — non-zero просто возвращается, без exception."""
    from server.provisioner.ssh import ssh_connect

    host, port, password = sshd_container
    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        result = await ssh.run(command="exit 13", check=False)
    assert result.returncode == 13


async def test_ssh_write_file_creates_and_chmods(sshd_container: tuple[str, int, str]) -> None:
    """write_file пишет содержимое + chmod, читается через cat."""
    from server.provisioner.ssh import ssh_connect

    host, port, password = sshd_container
    target_path = f"/tmp/waygate-it-{uuid.uuid4().hex[:8]}.conf"
    content = "key=value\nempty=\n# комментарий с UTF-8: ёжик\n"

    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        await ssh.write_file(path=target_path, content=content, mode="0640")
        result = await ssh.run(command=f"cat '{target_path}'")
        mode_result = await ssh.run(command=f"stat -c '%a' '{target_path}'")

    assert result.stdout == content
    assert mode_result.stdout.strip() == "640"


async def test_ssh_write_file_handles_quotes_in_content(sshd_container: tuple[str, int, str]) -> None:
    """heredoc-quoting корректно обрабатывает single quotes и `$` внутри content'а.

    Регрессия: раньше при `cat > file <<EOF` без single-quoted heredoc
    `$VAR` интерпретировался шеллом target'а. Используем `<<'EOF'` —
    проверяем что `$HOME`, ' и прочие spec'ы доходят без модификаций.
    """
    from server.provisioner.ssh import ssh_connect

    host, port, password = sshd_container
    target_path = f"/tmp/waygate-it-quotes-{uuid.uuid4().hex[:8]}.conf"
    content = "value with 'single quotes' and $HOME literal and $(echo bad) too\n"

    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        await ssh.write_file(path=target_path, content=content)
        result = await ssh.run(command=f"cat '{target_path}'")

    assert result.stdout == content


async def test_ssh_ensure_root_noop_for_root_user(sshd_container: tuple[str, int, str]) -> None:
    """ensure_root_or_sudo — для root юзера noop, sudo-prefix не включается."""
    from server.provisioner.ssh import ssh_connect

    host, port, password = sshd_container
    async with ssh_connect(host=host, port=port, username="root", password=password) as ssh:
        await ssh.ensure_root_or_sudo()
        result = await ssh.run(command="echo no-sudo-here")
    assert result.returncode == 0
    assert "no-sudo-here" in result.stdout
