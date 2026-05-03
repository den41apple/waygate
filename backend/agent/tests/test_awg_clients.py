import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from agent import awg_clients as module
from agent.awg_clients import (
    AwgClientError,
    delete_client,
    deploy_client,
    list_managed_clients,
    start_client,
    stop_client,
)
from agent.config import settings as agent_settings
from agent.subprocess_runner import CommandError
from shared.schemas import AwgClientStatus

# Валидные base64-32-байтные ключи для fixture-конфигов.
_PRIV = "Wyw1Tr4L/NV0SKMDjNtwhAKgQQkY/NlMXhwRjZrVQ4o="
_PUB = "k6E1U4ZvkV8Lxay5d8HvPCtHsO0XG6iZzQOvmW+qWrY="

_VALID_CONFIG = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_PRIV}

[Peer]
PublicKey = {_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""


@pytest.fixture
def fake_clients_dir(tmp_path: Path, monkeypatch) -> Path:
    """Изолируем `/etc/waygate/clients` под tmp_path для теста."""
    monkeypatch.setattr(agent_settings, "clients_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_run(monkeypatch):
    """Перехватывает `run_command`. По умолчанию возвращает пустой stdout, можно
    переопределить через `responses` (mapping prefix→stdout)."""

    # Внутри значения разных типов (list[list[str]], dict[str, str], set[str]) —
    # union mypy не narrowing'ит по ключу, а правка всех call-сайтов на dataclass
    # дороже чем `Any` в тестовом state.
    state: dict[str, Any] = {"calls": [], "responses": {}, "raise_on": set()}

    async def _runner(command: Iterable[str], *, stdin: bytes | None = None, check: bool = True) -> str:
        cmd = list(command)
        state["calls"].append(cmd)
        joined = " ".join(cmd)
        if joined in state["raise_on"]:
            raise CommandError(command=cmd, returncode=1, stderr="forced error")
        for prefix, response in state["responses"].items():
            if joined.startswith(prefix):
                return str(response)
        return ""

    monkeypatch.setattr(module, "run_command", _runner)
    return state


async def test_deploy_client_writes_config_and_runs_docker(fake_clients_dir, fake_run):
    info = await deploy_client(name="us-fast", config_text=_VALID_CONFIG)

    # Конфиг записан как `<iface>.conf` (awg-quick парсит имя интерфейса из basename),
    # с chmod 600 чтобы PrivateKey не утёк через other-readable.
    config_path = fake_clients_dir / "us-fast" / "awg-us-fast.conf"
    assert config_path.exists()
    assert oct(config_path.stat().st_mode)[-3:] == "600"
    written = config_path.read_text()
    assert "Address = 10.66.66.2/24" in written
    assert _PRIV in written

    # Был docker run с правильными флагами
    docker_runs = [c for c in fake_run["calls"] if c[:2] == ["docker", "run"]]
    assert len(docker_runs) == 1
    cmd = docker_runs[0]
    # --network host: netdev появляется на хосте, можно роутить host-трафик через него
    assert "--network" in cmd and "host" in cmd
    assert "--cap-add" in cmd and "NET_ADMIN" in cmd
    assert "--device" in cmd
    # ENV IFACE=<iface> — awg-quick подхватит правильный конфиг
    assert "IFACE=awg-us-fast" in cmd
    assert "io.waygate.role=client" in cmd
    assert "io.waygate.client-name=us-fast" in cmd
    assert "io.waygate.client-iface=awg-us-fast" in cmd
    assert "waygate-amnezia-client-us-fast" in cmd

    # Также мы делали pre-cleanup netdev'а в host-netns
    ip_link_dels = [c for c in fake_run["calls"] if c[:3] == ["ip", "link", "delete"]]
    assert ["ip", "link", "delete", "awg-us-fast"] in ip_link_dels

    # Возвращённый info содержит данные из [Peer]
    assert info.name == "us-fast"
    assert info.peer_endpoint == "vpn.example.com:51820"
    assert info.peer_pubkey == _PUB
    assert info.status is AwgClientStatus.RUNNING


async def test_deploy_client_iface_name_truncated_to_15_chars(fake_clients_dir, fake_run):
    """Linux IFNAMSIZ=16 → имя netdev ≤15 символов. `awg-<name>[:11]` гарантирует это
    даже для длинных пользовательских имён."""
    long_name = "very-long-client-name-here"  # 26 chars — длиннее IFNAMSIZ
    info = await deploy_client(name=long_name, config_text=_VALID_CONFIG)

    expected_iface = "awg-very-long-c"  # 4 + 11 = 15 chars
    assert len(expected_iface) == 15

    config_path = fake_clients_dir / long_name / f"{expected_iface}.conf"
    assert config_path.exists()

    docker_runs = [c for c in fake_run["calls"] if c[:2] == ["docker", "run"]]
    assert f"IFACE={expected_iface}" in docker_runs[0]
    assert f"io.waygate.client-iface={expected_iface}" in docker_runs[0]
    assert info.status is AwgClientStatus.RUNNING


async def test_deploy_client_rejects_invalid_config(fake_clients_dir, fake_run):
    with pytest.raises(AwgClientError, match="невалидный"):
        await deploy_client(name="bad", config_text="[Interface]\nAddress = ...")


async def test_deploy_client_removes_old_container_first(fake_clients_dir, fake_run):
    """Идемпотентность: повторный deploy на тоже имя не падает, удаляет старый контейнер."""
    await deploy_client(name="us-fast", config_text=_VALID_CONFIG)
    await deploy_client(name="us-fast", config_text=_VALID_CONFIG)

    # Должно быть минимум 2 docker rm -f и 2 docker run
    rm_calls = [c for c in fake_run["calls"] if c[:3] == ["docker", "rm", "-f"]]
    run_calls = [c for c in fake_run["calls"] if c[:2] == ["docker", "run"]]
    assert len(rm_calls) == 2
    assert len(run_calls) == 2


async def test_list_managed_clients_filters_by_label(fake_clients_dir, fake_run):
    # Подготавливаем .conf файл — list_managed читает его для метаданных.
    # Файл называется по новой схеме `<iface>.conf` (см. _config_path).
    (fake_clients_dir / "us-fast").mkdir()
    (fake_clients_dir / "us-fast" / "awg-us-fast.conf").write_text(_VALID_CONFIG)

    fake_run["responses"]["docker ps"] = (
        "\n".join(
            [
                json.dumps({"Names": "waygate-amnezia-client-us-fast", "State": "running"}),
                json.dumps({"Names": "some-other-container", "State": "running"}),
            ]
        )
        + "\n"
    )

    clients = await list_managed_clients()

    assert len(clients) == 1
    assert clients[0].name == "us-fast"
    assert clients[0].status is AwgClientStatus.RUNNING
    assert clients[0].peer_endpoint == "vpn.example.com:51820"

    # Фильтр по label: команда должна была пройти `--filter label=io.waygate.role=client`
    list_calls = [c for c in fake_run["calls"] if c[:2] == ["docker", "ps"]]
    assert any("--filter" in c and "label=io.waygate.role=client" in c for c in list_calls)


async def test_start_stop_client(fake_clients_dir, fake_run):
    status = await start_client(name="us-fast")
    assert status is AwgClientStatus.RUNNING
    assert ["docker", "start", "waygate-amnezia-client-us-fast"] in fake_run["calls"]

    status = await stop_client(name="us-fast")
    assert status is AwgClientStatus.STOPPED
    assert ["docker", "stop", "waygate-amnezia-client-us-fast"] in fake_run["calls"]


async def test_delete_client_removes_container_netdev_and_config(fake_clients_dir, fake_run):
    """С --network host netdev переживает `docker rm -f` — чистим явно `ip link delete`."""
    (fake_clients_dir / "us-fast").mkdir()
    (fake_clients_dir / "us-fast" / "awg-us-fast.conf").write_text(_VALID_CONFIG)

    await delete_client(name="us-fast")

    # Контейнер снесён, netdev удалён, папка удалена
    assert ["docker", "rm", "-f", "waygate-amnezia-client-us-fast"] in fake_run["calls"]
    assert ["ip", "link", "delete", "awg-us-fast"] in fake_run["calls"]
    assert not (fake_clients_dir / "us-fast").exists()
