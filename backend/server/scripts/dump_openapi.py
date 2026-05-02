"""Сериализует OpenAPI-схему control-plane в JSON.

Запуск:
    cd backend && uv run python -m server.scripts.dump_openapi > server/openapi.json

Не требует поднятого backend'а — берёт схему прямо из FastAPI-app через app.openapi().
В CI используется чтобы убедиться что коммитнутые TS-типы не отстали от Pydantic-схем.
"""

import json
import sys

from server.main import create_app


def main() -> None:
    app = create_app()
    sys.stdout.write(json.dumps(app.openapi(), indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
