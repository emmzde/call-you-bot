#!/usr/bin/env bash

if [[ -z "${BASH_VERSION:-}" ]]; then
    printf 'Этот скрипт нужно запускать через bash.\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME_FILE="${PROJECT_NAME_FILE:-/etc/call-you-bot/project-name}"
if [[ -z "${COMPOSE_PROJECT_NAME:-}" && -r "${PROJECT_NAME_FILE}" ]]; then
    COMPOSE_PROJECT_NAME="$(<"${PROJECT_NAME_FILE}")"
fi
if [[ -z "${COMPOSE_PROJECT_NAME:-}" ]] && command -v docker >/dev/null 2>&1; then
    COMPOSE_PROJECT_NAME="$(
        {
            docker ps -a \
                --filter label=com.tebya-zovut-bot.managed=true \
                --filter label=com.docker.compose.service=bot \
                --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null || true
        } |
            head -n 1
    )"
fi
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-call-you-bot}"
[[ "${COMPOSE_PROJECT_NAME}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || {
    printf 'Некорректное имя Compose project: %s\n' "${COMPOSE_PROJECT_NAME}" >&2
    exit 1
}
COMPOSE_FILES=(
    -f "${PROJECT_DIR}/docker-compose.yml"
    -f "${PROJECT_DIR}/docker-compose.secrets.yml"
)
BOT_IMAGE="${BOT_IMAGE:-call-you-bot:production}"
PREBUILT_IMAGE_REPOSITORY="${PREBUILT_IMAGE_REPOSITORY:-ghcr.io/emmzde/call-you-bot}"
OPS_CONFIG_FILE="${OPS_CONFIG_FILE:-/etc/call-you-bot/ops.env}"
export BOT_IMAGE COMPOSE_PROJECT_NAME PREBUILT_IMAGE_REPOSITORY

log() {
    printf '\033[1;32m[OK]\033[0m %s\n' "$*"
}

info() {
    printf '\033[1;34m[INFO]\033[0m %s\n' "$*"
}

warn() {
    printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2
}

die() {
    printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2
    exit 1
}

compose() {
    docker compose --project-name "${COMPOSE_PROJECT_NAME}" \
        "${COMPOSE_FILES[@]}" "$@"
}

load_ops_config() {
    if [[ -r "${OPS_CONFIG_FILE}" ]]; then
        # The installer creates this file root-owned with mode 0600.
        # shellcheck disable=SC1090
        source "${OPS_CONFIG_FILE}"
    fi
}

ping_heartbeat() {
    local url="${1:-}"
    [[ -n "${url}" ]] || return 0
    [[ "${url}" == https://* ]] || {
        warn "Heartbeat URL должен использовать HTTPS."
        return 1
    }
    curl --fail --silent --show-error \
        --connect-timeout 5 --max-time 15 --retry 2 \
        --output /dev/null "${url}"
}

acquire_operations_lock() {
    if [[ "${OPERATIONS_LOCK_HELD:-0}" == "1" ]]; then
        return 0
    fi
    mkdir -p /run/lock
    exec {OPERATIONS_LOCK_FD}>/run/lock/call-you-bot-operations.lock
    if ! flock -n "${OPERATIONS_LOCK_FD}"; then
        return 1
    fi
    export OPERATIONS_LOCK_HELD=1
}

git_safe() {
    git \
        -c "safe.directory=${PROJECT_DIR}" \
        -c http.lowSpeedLimit=1024 \
        -c http.lowSpeedTime=60 \
        -C "${PROJECT_DIR}" "$@"
}

bot_container_id() {
    compose ps -q bot 2>/dev/null || true
}

show_bot_logs() {
    compose logs --no-color --tail=100 bot 2>/dev/null || true
}

wait_for_bot_health() {
    local timeout_seconds="${1:-180}"
    local deadline=$((SECONDS + timeout_seconds))
    local container_id=""
    local container_status=""
    local health_status=""

    while ((SECONDS < deadline)); do
        container_id="$(bot_container_id)"
        if [[ -z "${container_id}" ]]; then
            sleep 2
            continue
        fi

        container_status="$(
            docker inspect --format '{{.State.Status}}' "${container_id}" 2>/dev/null ||
                true
        )"
        health_status="$(
            docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
                "${container_id}" 2>/dev/null || true
        )"

        if [[ "${container_status}" == "running" && "${health_status}" == "healthy" ]]; then
            return 0
        fi
        if [[ "${container_status}" == "exited" || "${container_status}" == "dead" ]]; then
            return 1
        fi
        sleep 2
    done
    return 1
}

telegram_is_healthy() {
    compose exec -T bot python -m tebya_zovut_bot.healthcheck \
        --telegram-only >/dev/null 2>&1
}

wait_for_telegram_health() {
    local timeout_seconds="${1:-180}"
    local deadline=$((SECONDS + timeout_seconds))

    while ((SECONDS < deadline)); do
        if telegram_is_healthy; then
            return 0
        fi
        sleep 5
    done
    return 1
}

source_revision() {
    if git_safe rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git_safe rev-parse HEAD
    else
        printf 'standalone-%s\n' "$(date -u +%Y%m%d%H%M%S)"
    fi
}

build_production_image() {
    local revision="$1"
    timeout --signal=TERM --kill-after=30s 40m docker build \
        --pull \
        --build-arg "VCS_REF=${revision}" \
        --label com.tebya-zovut-bot.managed=true \
        --label "com.tebya-zovut-bot.revision=${revision}" \
        --tag "${BOT_IMAGE}" \
        "${PROJECT_DIR}"
}

pull_prebuilt_image() {
    local revision="$1"
    local candidate
    local image_revision

    [[ "${revision}" =~ ^[0-9a-f]{40,64}$ ]] || return 1
    candidate="${PREBUILT_IMAGE_REPOSITORY}:${revision}"
    info "Пробую готовый проверенный образ ${candidate}…"
    if ! timeout --signal=TERM --kill-after=15s 5m docker pull "${candidate}"; then
        warn "Готовый образ недоступен; выполню локальную сборку."
        return 1
    fi
    image_revision="$(
        docker image inspect --format \
            '{{index .Config.Labels "com.tebya-zovut-bot.revision"}}' \
            "${candidate}" 2>/dev/null || true
    )"
    if [[ "${image_revision}" != "${revision}" ]]; then
        warn "Revision готового образа не совпадает с исходным кодом; образ отклонён."
        return 1
    fi
    docker tag "${candidate}" "${BOT_IMAGE}"
    log "Готовый production-образ проверен; долгая сборка не требуется."
}

prepare_production_image() {
    local revision="$1"
    if pull_prebuilt_image "${revision}"; then
        return 0
    fi
    info "Собираю production-образ локально…"
    build_production_image "${revision}"
}
