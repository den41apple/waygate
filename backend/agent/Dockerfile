# Dev-образ агента. Сборка из корня репо:
#   docker build -f backend/agent/Dockerfile -t waygate-agent .
# Прод-инсталляция работает через wheel + systemd, не через Docker.
FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ipset \
        iptables \
        iproute2 \
        dnsmasq \
        wireguard-tools \
        ca-certificates \
        curl \
        gnupg \
        qrencode \
        && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI ставим из docker-ce-cli repo, не из debian'овского `docker.io`:
# в slim-Debian 13 пакет docker.io содержит только daemon (без `/usr/bin/docker`),
# а агенту нужен именно CLI чтобы делать `docker ps`/`run`/`inspect`. Прод ставит
# через get.docker.com — здесь повторяем их ключ + repo для consistency.
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"${VERSION_CODENAME}\") stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY backend/ /app/backend/

WORKDIR /app/backend
# Не плодим .venv — пакеты в system Python (контейнер уже изолирован).
ENV UV_PROJECT_ENVIRONMENT=/usr/local
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
RUN uv sync --all-packages

ENV PORT=7743
ENV LOG_LEVEL=INFO
EXPOSE 7743

CMD ["uv", "run", "granian", "--interface", "asgi", "agent.main:app", "--host", "0.0.0.0", "--port", "7743"]
