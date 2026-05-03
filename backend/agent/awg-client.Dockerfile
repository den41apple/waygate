# Образ для AmneziaWG-клиента, развёртываемого Waygate-агентом.
# Тонкий слой над Alpine с amneziawg-tools/-go: внутри запускается `awg-quick up`
# с конфигом из bind-mounted /etc/amnezia/awg0.conf.
#
# Сборка: docker build -f backend/agent/awg-client.Dockerfile -t waygate-awg-client:dev backend/agent/
#
# Используется агентом — `docker run` с:
#   --cap-add NET_ADMIN --device /dev/net/tun
#   -v /etc/waygate/clients/<name>/:/etc/amnezia/
#   --label io.waygate.role=client

FROM alpine:3.20

# amneziawg-tools и amneziawg-go доступны в community/edge — у Alpine 3.20 они
# в community начиная с конца 2024. iptables/iproute2 для wg-quick PostUp/PreDown.
# openresolv для DNS = ... — wg-quick через resolvectl/openresolv применяет.
# Tini — корректный PID 1, прокидывает SIGTERM в awg-quick down.
RUN apk add --no-cache \
        amneziawg-tools \
        amneziawg-go \
        iptables \
        ip6tables \
        iproute2 \
        openresolv \
        tini \
        bash

LABEL io.waygate.role="client" \
      org.opencontainers.image.title="waygate-awg-client" \
      org.opencontainers.image.source="https://github.com/den41apple/waygate"

# /etc/amnezia/awg0.conf — точка монтирования. Bind'ится агентом при `docker run`.
WORKDIR /etc/amnezia

# Tini обрабатывает SIGTERM от docker stop корректно: останавливает awg-quick,
# который в свою очередь сносит интерфейс и iptables-правила.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["sh", "-c", "awg-quick up /etc/amnezia/awg0.conf && trap 'awg-quick down /etc/amnezia/awg0.conf; exit 0' TERM; sleep infinity & wait"]
