#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

# shellcheck source=deploy/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

command -v docker >/dev/null 2>&1 || die "Docker не установлен."
docker info >/dev/null 2>&1 || die "Docker daemon недоступен."

container_id="$(bot_container_id)"
if [[ -z "${container_id}" ]]; then
    die "Контейнер бота не найден. Запустите deploy/install.sh."
fi

container_status="$(
    docker inspect --format '{{.State.Status}}' "${container_id}" 2>/dev/null || true
)"
health_status="$(
    docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
        "${container_id}" 2>/dev/null || true
)"

printf 'Контейнер: %s\n' "${container_status:-unknown}"
printf 'Самопроверка: %s\n' "${health_status:-unknown}"
printf '\nПоследние события:\n'
show_bot_logs

if [[ "${container_status}" == "running" && "${health_status}" == "healthy" ]]; then
    printf '\n'
    log "Бот работает нормально."
    exit 0
fi

die "Бот требует внимания. Отправьте этот вывод разработчику."
