"""Список всех docker-контейнеров на target — для UI выбора `scope_target`.

Когда оператор настраивает Direction со scope=container, ему нужно ввести имя
контейнера в чьём netns применять правила. Раньше это был свободный text-input,
и опечатка/несуществующий контейнер обнаруживались только при Apply (когда
агент возвращал "контейнер X не запущен"). Теперь UI получает реальный список
через этот endpoint и подставляет его в dropdown.

Помечаем waygate-managed контейнеры (waygate-amnezia-client-*) — у них особая
семантика: их netns с awg-quick'ом, и применять туда policy-routing обычно не
имеет смысла. UI их рисует серым в выпадайке.
"""

import json

from loguru import logger

from agent.awg_clients import (
    _CONTAINER_NAME_PREFIX,
    _docker_status_to_state,
)
from agent.subprocess_runner import CommandError, run_command
from shared.schemas import ContainerInfo


async def list_all_containers() -> list[ContainerInfo]:
    """Возвращает все running и stopped docker-контейнеры на target.

    `docker ps --all` для полноты — UI сразу подсвечивает остановленные контейнеры
    (нельзя на них apply'ить, надо запустить). Без `--filter` — показываем всё,
    включая системные/чужие контейнеры; пользователь сам выбирает что нужно.
    """
    try:
        output = await run_command(
            ["docker", "ps", "--all", "--format", "{{json .}}"],
            check=False,
        )
    except CommandError as exc:
        logger.warning("containers: docker ps упал: {}", exc)
        return []

    result: list[ContainerInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("containers: пропускаю невалидную строку docker ps: {}", line)
            continue
        name = container.get("Names", "") or ""
        if not name:
            continue
        result.append(
            ContainerInfo(
                name=name,
                status=_docker_status_to_state(status=container.get("State", "") or container.get("Status", "")),
                image=container.get("Image", "") or "",
                is_waygate_managed=name.startswith(_CONTAINER_NAME_PREFIX),
            ),
        )
    return result
