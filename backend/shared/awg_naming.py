"""Общие helpers для именования AWG-сущностей — netdev, контейнер.

Живёт в `shared/` потому что и agent (который реально создаёт netdev'ы), и
server (который отдаёт `interface_name` в API), и фронт (через автогенерённые
TS-типы) должны знать одну и ту же формулу. Linux IFNAMSIZ=16 — netdev-имя
должно быть ≤15 символов, отсюда префикс `awg-` (4) + 11 символов из имени
клиента.
"""

IFACE_PREFIX = "awg-"
IFACE_MAX_NAME_LEN = 11
CONTAINER_NAME_PREFIX = "waygate-amnezia-client-"


def iface_name_for(*, name: str) -> str:
    """`name='myclient'` → `'awg-myclient'`. Длинные имена обрезаются до 15 chars."""
    return f"{IFACE_PREFIX}{name[:IFACE_MAX_NAME_LEN]}"


def container_name_for(*, name: str) -> str:
    """`name='myclient'` → `'waygate-amnezia-client-myclient'`."""
    return f"{CONTAINER_NAME_PREFIX}{name}"
