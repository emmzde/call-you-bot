#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

# shellcheck source=deploy/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_ops_config

BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-/var/backups/call-you-bot}"
MONITOR_HEARTBEAT_URL="${MONITOR_HEARTBEAT_URL:-}"
BACKUP_HEARTBEAT_URL="${BACKUP_HEARTBEAT_URL:-}"

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
telegram_status="unavailable"
if [[ "${container_status}" == "running" ]] && telegram_is_healthy; then
    telegram_status="healthy"
fi
printf 'Связь с Telegram: %s\n' "${telegram_status}"
reconcile_timer="$(systemctl is-active call-you-bot-reconcile.timer 2>/dev/null || true)"
backup_timer="$(systemctl is-active call-you-bot-backup.timer 2>/dev/null || true)"
printf 'Самовосстановление: %s\n' "${reconcile_timer:-unknown}"
printf 'Автоматические backups: %s\n' "${backup_timer:-unknown}"

latest_backup=""
if [[ -d "${BACKUP_DIRECTORY}" ]]; then
    latest_backup="$(
        find "${BACKUP_DIRECTORY}" -maxdepth 1 -type f \
            -name 'bot-*.sqlite3.gz' -printf '%T@ %p\n' 2>/dev/null |
            sort -nr | sed -n '1{s/^[^ ]* //;p;}'
    )"
fi
backup_fresh=0
if [[ -n "${latest_backup}" ]]; then
    backup_age_seconds=$(( $(date +%s) - $(stat -c %Y "${latest_backup}") ))
    printf 'Последний backup: %s (%s ч. назад)\n' \
        "${latest_backup}" "$((backup_age_seconds / 3600))"
    if ((backup_age_seconds <= 172800)); then
        backup_fresh=1
    fi
else
    printf 'Последний backup: не найден\n'
fi

if [[ -z "${MONITOR_HEARTBEAT_URL}" || -z "${BACKUP_HEARTBEAT_URL}" ]]; then
    warn "Внешние heartbeat URL настроены не полностью; падение всего VPS может остаться незамеченным."
else
    printf 'Внешний мониторинг: настроен\n'
fi
printf '\nПоследние события:\n'
show_bot_logs

if [[ "${container_status}" == "running" && "${health_status}" == "healthy" && \
    "${telegram_status}" == "healthy" && \
    "${reconcile_timer}" == "active" && "${backup_timer}" == "active" && \
    "${backup_fresh}" -eq 1 ]]; then
    printf '\n'
    log "Бот работает нормально."
    exit 0
fi

die "Бот требует внимания. Отправьте этот вывод разработчику."
