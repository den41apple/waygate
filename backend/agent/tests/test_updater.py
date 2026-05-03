import asyncio

import pytest

from agent import updater


@pytest.fixture(autouse=True)
def _stub_swap(monkeypatch):
    """Не запускаем реальный bash/setsid в тестах. Записываем что spawn'или."""
    spawned: list[str] = []

    async def fake_spawn(*, script_path):
        spawned.append(str(script_path))

    monkeypatch.setattr(updater, "_spawn_detached_swap", fake_spawn)
    return spawned


async def test_validate_version_rejects_dangerous_strings():
    with pytest.raises(updater.UpdateError):
        updater._validate_version(version="0.1.0; rm -rf /")


async def test_update_agent_writes_swap_script_and_spawns(monkeypatch, _stub_swap):
    """Happy-path: скачиваем wheel, пишем swap-скрипт, отвязанно spawn'им его."""
    downloaded: list[str] = []

    async def fake_download(*, url: str, target):
        downloaded.append(url)
        target.write_bytes(b"PK\x03\x04 fake wheel content")

    monkeypatch.setattr(updater, "_download_wheel", fake_download)

    from shared.schemas import UpdateRequest

    response = await updater.update_agent(
        request=UpdateRequest(version="0.2.0", wheel_url="https://github.com/example/wheel.whl"),
    )
    # Дать spawn-таске долететь до fake_spawn
    await asyncio.sleep(0.05)

    assert response.previous_version
    assert response.status.value == "restarting"
    assert downloaded == ["https://github.com/example/wheel.whl"]
    # Скрипт записан и был передан в spawn
    assert _stub_swap == [str(updater._SWAP_SCRIPT_PATH)]
    script_text = updater._SWAP_SCRIPT_PATH.read_text()
    # Все ключевые шаги swap'а должны быть в скрипте
    assert "python3 -m venv" in script_text
    assert "/bin/pip" in script_text and "install" in script_text
    assert "/opt/waygate-agent.new" in script_text
    assert "/opt/waygate-agent.bak" in script_text
    assert "systemctl restart waygate-agent" in script_text
    # И сам wheel-путь должен быть упомянут
    assert "/tmp/waygate_agent-0.2.0-py3-none-any.whl" in script_text
    # Логи перенаправлены в файл для диагностики при падении (агент в этот момент
    # рестартует, journal'у не доверяем).
    assert "/var/log/waygate-update.log" in script_text
    assert "set -ex" in script_text  # трейс команд в логе
