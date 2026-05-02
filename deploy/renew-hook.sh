#!/usr/bin/env bash
# Хук для перечитывания TLS-сертификата агентом без даунтайма.
#
# Вызывается автоматически после успешного обновления сертификата —
# например, из certbot post-hook'а или из Let's Encrypt-таймера.
#
# Granian (как и большинство ASGI-серверов) перечитывает SSL-сертификат
# по сигналу SIGUSR1 на главный процесс, а воркеры рестартятся бесшовно.
#
# Использование:
#   echo "deploy/renew-hook.sh" >> /etc/letsencrypt/renewal-hooks/post/waygate
#   chmod +x deploy/renew-hook.sh
set -euo pipefail

SERVICE_NAME="${WAYGATE_AGENT_SERVICE:-waygate-agent.service}"

PID="$(systemctl show -p MainPID --value "${SERVICE_NAME}")"
if [[ -z "${PID}" || "${PID}" == "0" ]]; then
    echo "renew-hook: ${SERVICE_NAME} не запущен — пропускаю reload" >&2
    exit 0
fi

kill -USR1 "${PID}"
echo "renew-hook: SIGUSR1 → ${SERVICE_NAME} (PID=${PID})"
