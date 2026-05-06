import pytest

from server.provisioner import steps
from server.provisioner.ssh import CommandResult, SshSession


def test_ssh_wrap_no_sudo_returns_command_unchanged():
    session = SshSession.__new__(SshSession)
    session._sudo_prefix = ""
    assert session._wrap("apt-get update") == "apt-get update"


def test_ssh_wrap_with_sudo_wraps_in_sh_c():
    """sudo-режим оборачивает в `sudo -n sh -c '<cmd>'` — чтобы redirect/heredoc/pipe
    обработались уже под root'ом, а не локальным юзером."""
    session = SshSession.__new__(SshSession)
    session._sudo_prefix = "sudo -n "
    assert session._wrap("apt-get update") == "sudo -n sh -c 'apt-get update'"


def test_ssh_wrap_escapes_single_quotes():
    """Если в команде есть `'` (например heredoc `<<'EOF'`) — экранируем
    POSIX-trick'ом `'\\''` чтобы внешние одинарные кавычки sh -c не сломались."""
    session = SshSession.__new__(SshSession)
    session._sudo_prefix = "sudo -n "
    wrapped = session._wrap("echo 'hi'")
    assert wrapped == "sudo -n sh -c 'echo '\\''hi'\\'''"


class FakeSshSession(SshSession):
    """Записывает команды, отдаёт заготовки или дефолтный пустой ответ.

    Наследует `SshSession` чтобы mypy принимал `ssh=FakeSshSession()` там, где
    функция ждёт `SshSession`. Реальный `__init__` родителя пропускаем —
    тут не нужен реальный asyncssh.connection.
    """

    def __init__(self, *, responses: dict[str, CommandResult] | None = None):
        self.responses = responses or {}
        self.calls: list[str] = []
        self.files_written: list[tuple[str, str, str]] = []  # (path, content, mode)
        self.sudo_enabled = False

    def enable_sudo(self) -> None:
        self.sudo_enabled = True

    async def run(self, *, command: str, check: bool = True) -> CommandResult:
        self.calls.append(command)
        for prefix, response in self.responses.items():
            if command.startswith(prefix):
                if check and response.returncode != 0:
                    raise RuntimeError(f"fake fail: {command}")
                return response
        return CommandResult(returncode=0, stdout="", stderr="")

    async def write_file(self, *, path: str, content: str, mode: str = "0644") -> None:
        self.files_written.append((path, content, mode))


async def _no_emit(_message: str) -> None:
    return None


async def test_verify_os_accepts_ubuntu():
    ssh = FakeSshSession(
        responses={
            "cat /etc/os-release": CommandResult(
                returncode=0,
                stdout='NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\n',
                stderr="",
            ),
            "id -u": CommandResult(returncode=0, stdout="0\n", stderr=""),
        },
    )
    await steps.verify_os(ssh=ssh, emit=_no_emit)
    assert any("os-release" in call for call in ssh.calls)
    assert any("id -u" in call for call in ssh.calls)


async def test_verify_os_rejects_centos():
    ssh = FakeSshSession(
        responses={
            "cat /etc/os-release": CommandResult(
                returncode=0,
                stdout='NAME="CentOS Linux"\nID=centos\n',
                stderr="",
            ),
        },
    )
    with pytest.raises(steps.StepError):
        await steps.verify_os(ssh=ssh, emit=_no_emit)


async def test_verify_os_enables_sudo_when_nopasswd_available():
    """Не-root юзер + рабочий `sudo -n` → провижнер включает auto-sudo-режим
    у SshSession и продолжает онбординг (последующие команды обернутся в sudo)."""
    ssh = FakeSshSession(
        responses={
            "cat /etc/os-release": CommandResult(
                returncode=0,
                stdout='NAME="Ubuntu"\nID=ubuntu\n',
                stderr="",
            ),
            "id -u": CommandResult(returncode=0, stdout="1000\n", stderr=""),
            "sudo -n true": CommandResult(returncode=0, stdout="", stderr=""),
        },
    )
    await steps.verify_os(ssh=ssh, emit=_no_emit)
    assert ssh.sudo_enabled is True


async def test_verify_os_rejects_non_root_without_nopasswd_sudo():
    """Не-root + sudo требует пароль (returncode != 0 у `sudo -n`) → ошибка
    с инструкцией настроить NOPASSWD."""
    ssh = FakeSshSession(
        responses={
            "cat /etc/os-release": CommandResult(
                returncode=0,
                stdout='NAME="Ubuntu"\nID=ubuntu\n',
                stderr="",
            ),
            "id -u": CommandResult(returncode=0, stdout="1000\n", stderr=""),
            "sudo -n true": CommandResult(
                returncode=1,
                stdout="",
                stderr="sudo: a password is required\n",
            ),
        },
    )
    with pytest.raises(steps.StepError, match="NOPASSWD-sudo"):
        await steps.verify_os(ssh=ssh, emit=_no_emit)


async def test_install_deps_runs_apt():
    ssh = FakeSshSession()
    await steps.install_deps(ssh=ssh, emit=_no_emit)
    assert any("apt-get update" in call for call in ssh.calls)
    assert any("apt-get install" in call and "ipset" in call for call in ssh.calls)
    # NFT-3: install_deps должен переключить iptables alternative на nft-вариант.
    # Без этого agent пишет в legacy ip_tables module → правила не действуют под
    # Docker 28+. Команда idempotent через test+||.
    assert any("update-alternatives --set iptables /usr/sbin/iptables-nft" in call for call in ssh.calls), (
        f"iptables-nft alternative не переключается. ssh.calls = {ssh.calls}"
    )
    assert any("update-alternatives --set ip6tables /usr/sbin/ip6tables-nft" in call for call in ssh.calls), (
        f"ip6tables-nft alternative не переключается. ssh.calls = {ssh.calls}"
    )


async def test_detect_awg_containers_parses_lines():
    ssh = FakeSshSession(
        responses={
            "docker ps": CommandResult(returncode=0, stdout="awg-ru\nawg-ge\n", stderr=""),
        },
    )
    names = await steps.detect_awg_containers(ssh=ssh, emit=_no_emit)
    assert names == ["awg-ru", "awg-ge"]


async def test_detect_awg_containers_handles_empty():
    ssh = FakeSshSession()
    names = await steps.detect_awg_containers(ssh=ssh, emit=_no_emit)
    assert names == []


async def test_configure_dns_resolver_writes_resolv_conf_and_dnsmasq() -> None:
    """Шаг должен: отключить systemd-resolved, записать upstream.conf, зафиксировать resolv.conf."""
    ssh = FakeSshSession()
    await steps.configure_dns_resolver(ssh=ssh, emit=_no_emit)

    # systemd-resolved выключен (с check=False, не должно зависеть от existence)
    assert any("systemctl disable --now systemd-resolved" in call for call in ssh.calls)
    # /etc/resolv.conf зафиксирован nameserver 127.0.0.1
    paths_written = [path for path, _, _ in ssh.files_written]
    assert "/etc/resolv.conf" in paths_written
    resolv_content = next(content for path, content, _ in ssh.files_written if path == "/etc/resolv.conf")
    assert "nameserver 127.0.0.1" in resolv_content
    # dnsmasq upstream.conf — listen-address=127.0.0.1
    assert "/etc/dnsmasq.d/upstream.conf" in paths_written
    upstream_content = next(content for path, content, _ in ssh.files_written if path == "/etc/dnsmasq.d/upstream.conf")
    assert "listen-address=127.0.0.1" in upstream_content
    assert "no-resolv" in upstream_content
    # chattr +i — закрепили
    assert any("chattr +i /etc/resolv.conf" in call for call in ssh.calls)
    # restart dnsmasq
    assert any("systemctl restart dnsmasq" in call for call in ssh.calls)


async def test_deploy_agent_writes_env_and_unit():
    ssh = FakeSshSession()
    await steps.deploy_agent(
        ssh=ssh,
        token="secret-token",
        wheel_url="https://example.com/wheel.whl",
        agent_port=7743,
        emit=_no_emit,
    )
    paths_written = [path for path, _, _ in ssh.files_written]
    assert "/etc/waygate/agent.env" in paths_written
    assert "/etc/systemd/system/waygate-agent.service" in paths_written
    env_content = next(content for path, content, _ in ssh.files_written if path == "/etc/waygate/agent.env")
    assert "TOKEN=secret-token" in env_content
    assert "PORT=7743" in env_content
    assert any("systemctl daemon-reload" in call for call in ssh.calls)
    assert any("systemctl enable --now waygate-agent" in call for call in ssh.calls)
