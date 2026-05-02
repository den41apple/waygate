# Control-plane сервер. Сборка из корня репо:
#   docker build -f backend/server/Dockerfile -t waygate-server .
FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY backend/ /app/backend/

WORKDIR /app/backend
RUN uv sync --all-packages

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# При старте: alembic upgrade head + granian. cd в server чтобы alembic нашёл alembic.ini.
CMD ["sh", "-c", "cd server && uv run alembic upgrade head && cd /app/backend && uv run granian --interface asgi --http 1 server.main:app --host 0.0.0.0 --port 8000"]
