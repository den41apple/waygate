"""Backward-compat: AwgClientInfo принимает payload от старого агента без interface_name.

Регрессия в проде: после batch'а #20 server-side healthcheck.list_clients()
парсил ListAwgClientsResponse через Pydantic; старый агент (на
текущих managed-серверах) ещё не отдаёт `interface_name` — валидация падала
с `Field required`. Сделали поле Optional, server-side подставляет fallback
из shared/awg_naming.iface_name_for.
"""

from shared.schemas import AwgClientInfo, AwgClientStatus, ListAwgClientsResponse


def test_awg_client_info_accepts_payload_without_interface_name() -> None:
    """Старый агент шлёт без `interface_name` — Pydantic не должен ругаться."""
    payload = {
        "name": "nl",
        "container_name": "waygate-amnezia-client-nl",
        "status": "running",
        "peer_endpoint": "vpn.example.com:51820",
        "peer_pubkey": "k6E1U4ZvkV8Lxay5d8HvPCtHsO0XG6iZzQOvmW+qWrY=",
        "interface_address": "10.8.1.14/32",
    }
    info = AwgClientInfo.model_validate(payload)
    assert info.name == "nl"
    assert info.interface_name is None  # fallback должен подсунуть control-plane


def test_list_awg_clients_response_old_agent_payload() -> None:
    """Та же ситуация на уровне list-response (это конкретный path из healthcheck'а)."""
    payload = {
        "clients": [
            {
                "name": "nl",
                "container_name": "waygate-amnezia-client-nl",
                "status": "running",
                "peer_endpoint": None,
                "peer_pubkey": None,
                "interface_address": None,
            },
        ],
    }
    response = ListAwgClientsResponse.model_validate(payload)
    assert len(response.clients) == 1
    assert response.clients[0].interface_name is None


def test_awg_client_info_still_accepts_modern_payload() -> None:
    """Новый агент шлёт interface_name явно — должно работать как раньше."""
    info = AwgClientInfo(
        name="nl",
        container_name="waygate-amnezia-client-nl",
        interface_name="awg-nl",
        status=AwgClientStatus.RUNNING,
        peer_endpoint=None,
        peer_pubkey=None,
        interface_address=None,
    )
    assert info.interface_name == "awg-nl"
