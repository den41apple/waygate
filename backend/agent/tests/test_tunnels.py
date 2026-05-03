from agent.tunnels import _is_awg_container


def test_is_awg_container_matches_amnezia_awg_naming() -> None:
    """Регрессия: до фикса hints были ('amneziawg', 'amnezia-wg', 'amnezia/amneziawg'),
    и контейнер с именем `amnezia-awg2` не ловился — после онбординга
    `awg_containers` обнулялись агентом, в UI Routing/Tunnels пропадал список
    интерфейсов. Теперь совпадает с SSH-grep при онбординге."""
    assert _is_awg_container(image="amneziavpn/amnezia-server:latest", names="amnezia-awg2")
    assert _is_awg_container(image="amneziavpn/amneziawg-go", names="awg-eu-1")
    assert _is_awg_container(image="any/image", names="my-awg-tunnel")


def test_is_awg_container_ignores_unrelated() -> None:
    assert not _is_awg_container(image="postgres:16", names="pg-main")
    assert not _is_awg_container(image="nginx:1.27-alpine", names="edge-nginx")
    assert not _is_awg_container(image="redis:7", names="cache")
