#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

if ((EUID != 0)); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Нужны права root для самовосстановления бота.\n' >&2
        exit 1
    fi
    exec sudo /bin/bash "${SCRIPT_PATH}" "$@"
fi

# shellcheck source=deploy/common.sh
source "$(dirname -- "${SCRIPT_PATH}")/common.sh"
load_ops_config

STATE_DIRECTORY="${STATE_DIRECTORY:-/var/lib/call-you-bot}"
MONITOR_HEARTBEAT_URL="${MONITOR_HEARTBEAT_URL:-}"
MONITOR_FAILURE_URL="${MONITOR_FAILURE_URL:-}"
UNHEALTHY_RESTART_COOLDOWN_SECONDS="${UNHEALTHY_RESTART_COOLDOWN_SECONDS:-900}"
RESTART_STAMP="${STATE_DIRECTORY}/last-unhealthy-restart"

report_failure() {
    warn "$*"
    ping_heartbeat "${MONITOR_FAILURE_URL}" || true
}

report_healthy() {
    ping_heartbeat "${MONITOR_HEARTBEAT_URL}"
}

report_dependencies() {
    if telegram_is_healthy; then
        report_healthy
        return 0
    fi
    report_failure "Telegram API давно не отвечал; процесс оставлен запущенным для автоматического переподключения."
    return 1
}

recover_container() {
    info "Восстанавливаю production-контейнер бота…"
    if ! compose up -d --no-build --remove-orphans bot; then
        report_failure "Не удалось создать или запустить контейнер бота."
        return 1
    fi
    if wait_for_bot_health 180; then
        log "Контейнер восстановлен и прошёл healthcheck."
        report_dependencies
        return $?
    fi
    show_bot_logs
    report_failure "Контейнер запущен, но не прошёл healthcheck после восстановления."
    return 1
}

if ! acquire_operations_lock; then
    exit 0
fi

install -d -m 0700 "${STATE_DIRECTORY}"
[[ "${UNHEALTHY_RESTART_COOLDOWN_SECONDS}" =~ ^[0-9]+$ ]] || \
    die "UNHEALTHY_RESTART_COOLDOWN_SECONDS должен быть числом."
((UNHEALTHY_RESTART_COOLDOWN_SECONDS >= 60)) || \
    die "Cooldown между restart должен быть не меньше 60 секунд."

if ! docker info >/dev/null 2>&1; then
    report_failure "Docker daemon недоступен."
    exit 1
fi

container_id="$(bot_container_id)"
if [[ -z "${container_id}" ]]; then
    recover_container
    exit $?
fi

container_status="$(
    docker inspect --format '{{.State.Status}}' "${container_id}" 2>/dev/null || true
)"
health_status="$(
    docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
        "${container_id}" 2>/dev/null || true
)"

if [[ "${container_status}" == "running" && "${health_status}" == "healthy" ]]; then
    report_dependencies
    exit $?
fi

if [[ "${container_status}" != "running" ]]; then
    recover_container
    exit $?
fi

if [[ "${health_status}" == "starting" ]]; then
    if wait_for_bot_health 120; then
        report_dependencies
        exit $?
    fi
    health_status="unhealthy"
fi

if [[ "${health_status}" == "unhealthy" ]]; then
    now="$(date +%s)"
    last_restart=0
    if [[ -f "${RESTART_STAMP}" ]]; then
        last_restart="$(stat -c %Y "${RESTART_STAMP}" 2>/dev/null || printf '0')"
    fi
    if ((now - last_restart < UNHEALTHY_RESTART_COOLDOWN_SECONDS)); then
        report_failure "Контейнер остаётся unhealthy; повторный restart ограничен cooldown."
        exit 1
    fi

    touch "${RESTART_STAMP}"
    warn "Контейнер unhealthy; выполняю контролируемый restart."
    compose restart bot
    if wait_for_bot_health 180; then
        log "После restart контейнер снова healthy."
        report_dependencies
        exit $?
    fi
    show_bot_logs
    report_failure "Контейнер не восстановился после restart."
    exit 1
fi

report_failure "Неизвестное состояние контейнера: ${container_status}/${health_status:-none}."
exit 1
