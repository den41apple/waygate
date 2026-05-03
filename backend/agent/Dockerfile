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
        qrencode \
        docker.io \
        && \
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
