import asyncio

import pytest

from agent import updater


class _FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, _stdin=None):
        return self._stdout, self._stderr


@pytest.fixture(autouse=True)
def _fast_restart(monkeypatch):
    """Не делать реальной задержки и не запускать systemctl."""
    monkeypatch.setattr(updater, "_RESTART_DELAY_SECONDS", 0)

    async def fake_run(*, command):
        # systemctl не должен реально вызываться
        assert command[0] != "systemctl"

    monkeypatch.setattr(updater, "_run", fake_run)


async def test_validate_version_rejects_dangerous_strings():
    with pytest.raises(updater.UpdateError):
        updater._validate_version(version="0.1.0; rm -rf /")


async def test_update_agent_downloads_installs_and_returns_response(monkeypatch, tmp_path):
    downloaded: list[str] = []

    async def fake_download(*, url: str, target):
        downloaded.append(url)
        target.write_bytes(b"PK\x03\x04 fake wheel content")

    install_calls: list[list[str]] = []

    async def fake_run(*, command):
        install_calls.append(command)

    monkeypatch.setattr(updater, "_download_wheel", fake_download)
    monkeypatch.setattr(updater, "_run", fake_run)

    from shared.schemas import UpdateRequest

    response = await updater.update_agent(
        request=UpdateRequest(version="0.2.0", wheel_url="https://github.com/example/wheel.whl"),
    )
    # Дать фоновой restart-таске успеть завершиться (она вызывает fake_run на systemctl)
    await asyncio.sleep(0.05)

    assert response.previous_version
    assert response.status.value == "restarting"
    assert downloaded == ["https://github.com/example/wheel.whl"]
    assert any("install" in call for call in install_calls)
    # tmp_path используется как scratch — wheel-файл должен быть записан под него
    assert (tmp_path / "fake.whl").exists() is False  # мы записывали в /tmp/waygate-agent-... не сюда
