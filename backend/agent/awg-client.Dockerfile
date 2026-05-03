# Образ для AmneziaWG-клиента, развёртываемого Waygate-агентом.
# Внутри запускается `awg-quick up` с конфигом из bind-mounted /etc/amnezia/awg0.conf.
#
# Сборка: docker build -f backend/agent/awg-client.Dockerfile -t waygate-awg-client:dev backend/agent/
#
# Используется агентом — `docker run` с:
#   --cap-add NET_ADMIN --device /dev/net/tun
#   -v /etc/waygate/clients/<name>/:/etc/amnezia/
#   --label io.waygate.role=client
#
# AmneziaWG (форк wireguard с обфускацией Jc/Jmin/Jmax/S1-S4/H1-H4/I1-I5) не
# опубликован в репах Alpine, поэтому собираем `awg`/`awg-quick`/`amneziawg-go`
# из официальных репо amnezia-vpn — multi-stage чтобы итоговый образ остался
# тонким (только бинари без toolchain'а).

# ---------- Stage 1: amneziawg-tools (awg, awg-quick) ----------
FROM alpine:3.20 AS tools-build

RUN apk add --no-cache git make gcc musl-dev linux-headers bash

ARG AWG_TOOLS_REF=master
RUN git clone --depth 1 --branch "${AWG_TOOLS_REF}" \
        https://github.com/amnezia-vpn/amneziawg-tools.git /src
WORKDIR /src/src
# WITH_WGQUICK=yes ставит awg-quick (bash-скрипт), WITH_SYSTEMDUNITS=no — нам не нужен.
RUN make WITH_BASHCOMPLETION=no WITH_WGQUICK=yes WITH_SYSTEMDUNITS=no \
    && make DESTDIR=/output PREFIX=/usr WITH_BASHCOMPLETION=no \
        WITH_WGQUICK=yes WITH_SYSTEMDUNITS=no install


# ---------- Stage 2: amneziawg-go (userspace WireGuard для случая без kmod) ----------
FROM golang:1.24-alpine AS go-build

RUN apk add --no-cache git
ARG AWG_GO_REF=master
RUN git clone --depth 1 --branch "${AWG_GO_REF}" \
        https://github.com/amnezia-vpn/amneziawg-go.git /src
WORKDIR /src
RUN go mod download
RUN CGO_ENABLED=0 go build -ldflags '-w -s' -trimpath -o /amneziawg-go .


# ---------- Stage 3: тонкий runtime ----------
FROM alpine:3.20

# Runtime-зависимости: iptables/ip для wg-quick PostUp/PreDown, bash для awg-quick,
# openresolv для DNS = ... обработки, tini как корректный PID 1.
RUN apk add --no-cache \
        iptables \
        ip6tables \
        iproute2 \
        openresolv \
        bash \
        tini

# awg, awg-quick из tools-build
COPY --from=tools-build /output/usr/bin/awg /usr/bin/awg
COPY --from=tools-build /output/usr/bin/awg-quick /usr/bin/awg-quick
# amneziawg-go (userspace daemon — fallback если в kernel'е нет AWG kmod)
COPY --from=go-build /amneziawg-go /usr/bin/amneziawg-go

RUN chmod +x /usr/bin/awg /usr/bin/awg-quick /usr/bin/amneziawg-go

LABEL io.waygate.role="client" \
      org.opencontainers.image.title="waygate-awg-client" \
      org.opencontainers.image.source="https://github.com/den41apple/waygate"

WORKDIR /etc/amnezia

# `awg-quick up` поднимает интерфейс. На SIGTERM (от docker stop) trap снимает
# его — wg-quick'овый PreDown сам чистит iptables/routes.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["sh", "-c", "awg-quick up /etc/amnezia/awg0.conf && trap 'awg-quick down /etc/amnezia/awg0.conf; exit 0' TERM; sleep infinity & wait"]
