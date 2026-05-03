"""Conftest для агентских тестов — общие fixture'ы.

Помимо подкидывания TOKEN для in-process тестов, тут собран integration-fixture
`agent_container`, который поднимает реальный docker-контейнер с
ipset/iptables/dnsmasq и granian'ом, слушающим на 7743. Используется тестами с
маркером `@pytest.mark.integration`. Если docker недоступен — fixture делает
`pytest.skip()`.

Build выполняется один раз на pytest-сессию (session-scope). Контейнер шарится
между тестами — пишите изолированно по ipset-именам/контейнерам.
"""

import contextlib
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# Подкидываем токен ДО импорта приложения — agent.config.settings читает env при старте.
os.environ.setdefault("TOKEN", "test-token-1234567890")


_INTEGRATION_AGENT_TOKEN = "test-integration-token-dont-use-in-prod"
# Не на 7743 чтобы не конфликтовать с локально работающим агентом (если есть).
_INTEGRATION_AGENT_PORT_HOST = 27743
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _docker_available() -> bool:
    try:
        import docker
    except ImportError:
        return False
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return False
    return True


def _wait_for_http(*, base_url: str, token: str, timeout: float = 30.0) -> None:
    """Ждём пока granian реально начнёт отвечать на HTTP (TCP-handshake — мало,
    у granian'а accept-loop может ещё не быть готов в первый момент).
    """
    import httpx

    deadline = time.monotonic() + timeout
    last_err: BaseException | None = None
    headers = {"Authorization": f"Bearer {token}"}
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/v1/status", headers=headers, timeout=2.0)
            if response.status_code == 200:
                return
            last_err = RuntimeError(f"HTTP {response.status_code}: {response.text[:120]}")
        except (httpx.HTTPError, OSError) as exc:
            last_err = exc
        time.sleep(0.5)
    raise RuntimeError(f"agent-контейнер не отвечает на /v1/status за {timeout}s: {last_err}")


@pytest.fixture(scope="session")
def agent_image_tag() -> str:
    """Собирает образ `waygate-agent:integration-test` один раз на сессию."""
    if not _docker_available():
        pytest.skip("docker daemon недоступен — пропускаем integration-тесты")
    import docker

    client = docker.from_env()
    tag = "waygate-agent:integration-test"
    dockerfile_rel = "backend/agent/Dockerfile"
    client.images.build(path=str(_REPO_ROOT), dockerfile=dockerfile_rel, tag=tag, rm=True)
    return tag


class AgentContainer:
    """Удобная обёртка вокруг docker.Container — base URL + exec/copy."""

    def __init__(self, *, container: object, base_url: str) -> None:
        self.container = container
        self.base_url = base_url

    def exec(self, command: str) -> tuple[int, bytes]:
        """`docker exec`: возвращает (exit_code, stdout+stderr)."""
        result = self.container.exec_run(command)  # type: ignore[attr-defined]
        return result.exit_code, result.output


@pytest.fixture(scope="module")
def agent_container(agent_image_tag: str) -> Iterator[AgentContainer]:
    """Запускает контейнер с агентом, отдаёт обёртку с base URL и exec.

    `--privileged` чтобы ipset/iptables и `/proc/sys/net/ipv4/...` были
    writable (как в проде с CAP_NET_ADMIN). Порт явно пробрасывается на хост,
    `--network host` тут не нужен — netdev'ы внутри тоже исходные.
    """
    import docker

    client = docker.from_env()
    name = f"waygate-agent-it-{uuid.uuid4().hex[:8]}"
    container = client.containers.run(
        agent_image_tag,
        detach=True,
        privileged=True,
        name=name,
        environment={"TOKEN": _INTEGRATION_AGENT_TOKEN, "PORT": "7743", "LOG_LEVEL": "DEBUG"},
        ports={"7743/tcp": _INTEGRATION_AGENT_PORT_HOST},
        # /v1/status и /v1/tunnels вызывают `docker ps` для перечисления
        # AWG-контейнеров; в проде агент работает на хосте и видит host docker.
        # Тут пробрасываем socket чтобы те же endpoint'ы работали. ВАЖНО: тесты
        # не должны вызывать /v1/clients/deploy — иначе создадут контейнер на
        # ХОСТ-машине разработчика, а не в изолированной среде.
        volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
        # auto_remove=False — оставляем контейнер на teardown с stopped state
        # чтобы при проваленном тесте можно было смотреть `docker logs`. Чистим
        # вручную в finally.
        auto_remove=False,
    )
    base_url = f"http://127.0.0.1:{_INTEGRATION_AGENT_PORT_HOST}"
    try:
        try:
            _wait_for_http(base_url=base_url, token=_INTEGRATION_AGENT_TOKEN)
        except RuntimeError:
            # Не поднялся — выводим logs контейнера в stderr чтобы видно было.
            try:
                logs = container.logs(tail=50).decode(errors="replace")
                print(f"\n--- agent container logs ---\n{logs}\n--- /logs ---", flush=True)
            except Exception:
                pass
            raise
        yield AgentContainer(container=container, base_url=base_url)
    finally:
        with contextlib.suppress(Exception):
            container.kill()
        with contextlib.suppress(Exception):
            container.remove(force=True)


@pytest.fixture
def integration_agent_token() -> str:
    return _INTEGRATION_AGENT_TOKEN
