#!/usr/bin/env bash

if [[ -z "${BASH_VERSION:-}" ]]; then
    printf 'Этот скрипт нужно запускать через bash.\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILES=(
    -f "${PROJECT_DIR}/docker-compose.yml"
    -f "${PROJECT_DIR}/docker-compose.secrets.yml"
)
BOT_IMAGE="${BOT_IMAGE:-call-you-bot:production}"
export BOT_IMAGE

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
    docker compose "${COMPOSE_FILES[@]}" "$@"
}

git_safe() {
    git -c "safe.directory=${PROJECT_DIR}" -C "${PROJECT_DIR}" "$@"
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

backup_database() {
    local container_id
    local timestamp
    local target

    container_id="$(bot_container_id)"
    if [[ -z "${container_id}" ]]; then
        warn "Контейнер ещё не запущен — backup перед обновлением пропущен."
        return 0
    fi

    timestamp="$(date -u +%Y%m%d-%H%M%S)"
    target="/data/backups/pre-update-${timestamp}.sqlite3"
    if ! compose exec -T bot python -m tebya_zovut_bot.db_admin \
        backup "${target}"; then
        warn "Использую совместимый backup для старой версии бота."
        compose exec -T bot python - "${target}" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

source = Path(os.getenv("DATABASE_PATH", "/data/bot.sqlite3"))
target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
source_connection = sqlite3.connect(source)
target_connection = sqlite3.connect(target)
try:
    source_connection.backup(target_connection)
finally:
    target_connection.close()
    source_connection.close()
PY
    fi
    if ! compose exec -T bot python -m tebya_zovut_bot.db_admin \
        prune-backups /data/backups --days 14; then
        warn "Старая версия ещё не умеет очищать backup; продолжу обновление."
    fi
    log "Создан backup базы: ${target}"
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
    docker build \
        --pull \
        --label com.tebya-zovut-bot.managed=true \
        --label "com.tebya-zovut-bot.revision=${revision}" \
        --tag "${BOT_IMAGE}" \
        "${PROJECT_DIR}"
}
