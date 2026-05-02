import pytest

from server.provisioner import steps
from server.provisioner.ssh import CommandResult


class FakeSshSession:
    """Записывает команды, отдаёт заготовки или дефолтный пустой ответ."""

    def __init__(self, *, responses: dict[str, CommandResult] | None = None):
        self.responses = responses or {}
        self.calls: list[str] = []
        self.files_written: list[tuple[str, str, str]] = []  # (path, content, mode)

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
        },
    )
    await steps.verify_os(ssh=ssh, emit=_no_emit)
    assert any("os-release" in call for call in ssh.calls)


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


async def test_install_deps_runs_apt():
    ssh = FakeSshSession()
    await steps.install_deps(ssh=ssh, emit=_no_emit)
    assert any("apt-get update" in call for call in ssh.calls)
    assert any("apt-get install" in call and "ipset" in call for call in ssh.calls)


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
