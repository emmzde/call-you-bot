#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

if ((EUID != 0)); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Нужны права root для резервного копирования.\n' >&2
        exit 1
    fi
    exec sudo /bin/bash "${SCRIPT_PATH}" "$@"
fi

# shellcheck source=deploy/common.sh
source "$(dirname -- "${SCRIPT_PATH}")/common.sh"
load_ops_config

BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-/var/backups/call-you-bot}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_MIRROR_DIRECTORY="${BACKUP_MIRROR_DIRECTORY:-}"
BACKUP_MIRROR_REQUIRE_MOUNT="${BACKUP_MIRROR_REQUIRE_MOUNT:-true}"
BACKUP_HEARTBEAT_URL="${BACKUP_HEARTBEAT_URL:-}"
BACKUP_FAILURE_URL="${BACKUP_FAILURE_URL:-}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-}"

container_stage=""
host_stage=""
compressed_stage=""

report_failure() {
    ping_heartbeat "${BACKUP_FAILURE_URL}" || true
}

cleanup() {
    if [[ -n "${container_stage}" ]]; then
        compose exec -T bot rm -f -- "${container_stage}" >/dev/null 2>&1 || true
    fi
    [[ -n "${host_stage}" ]] && rm -f -- "${host_stage}"
    [[ -n "${compressed_stage}" ]] && rm -f -- "${compressed_stage}"
}

on_error() {
    local exit_code=$?
    cleanup
    report_failure
    warn "Резервное копирование завершилось ошибкой."
    exit "${exit_code}"
}
trap on_error ERR
trap cleanup EXIT

case "${BACKUP_DIRECTORY}" in
    /*) ;;
    *) die "BACKUP_DIRECTORY должен быть абсолютным путём." ;;
esac
[[ "${BACKUP_DIRECTORY}" != "/" ]] || die "Корневой каталог нельзя использовать для backups."
[[ "${BACKUP_RETENTION_DAYS}" =~ ^[0-9]+$ ]] || die "BACKUP_RETENTION_DAYS должен быть числом."
((BACKUP_RETENTION_DAYS >= 7)) || die "Храните локальные backups минимум 7 дней."

mkdir -p /run/lock
exec 9>/run/lock/call-you-bot-backup.lock
if ! flock -n 9; then
    info "Другое резервное копирование уже выполняется."
    exit 0
fi
acquire_operations_lock || die "Другая production-операция уже выполняется."

docker info >/dev/null
container_id="$(bot_container_id)"
[[ -n "${container_id}" ]] || die "Контейнер бота не найден."
container_status="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
[[ "${container_status}" == "running" ]] || die "Контейнер бота не запущен."

install -d -m 0700 "${BACKUP_DIRECTORY}"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
filename="bot-${timestamp}.sqlite3"
container_stage="/data/backups/.export-${timestamp}-$$.sqlite3"
host_stage="${BACKUP_DIRECTORY}/.${filename}.tmp"
compressed_stage="${BACKUP_DIRECTORY}/.${filename}.gz.tmp"
final_path="${BACKUP_DIRECTORY}/${filename}.gz"

compose exec -T bot mkdir -p /data/backups
if ! compose exec -T bot python -m tebya_zovut_bot.db_admin \
    backup "${container_stage}"; then
    warn "Использую совместимый SQLite backup для старой версии контейнера."
    compose exec -T bot python - "${container_stage}" <<'PY'
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
    result = target_connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {result!r}")
finally:
    target_connection.close()
    source_connection.close()
PY
fi
docker cp "${container_id}:${container_stage}" "${host_stage}"
compose exec -T bot rm -f -- "${container_stage}"
container_stage=""

gzip -9 -c -- "${host_stage}" >"${compressed_stage}"
gzip -t -- "${compressed_stage}"
mv -- "${compressed_stage}" "${final_path}"
compressed_stage=""
chmod 0600 "${final_path}"
rm -f -- "${host_stage}"
host_stage=""

if [[ -n "${BACKUP_MIRROR_DIRECTORY}" ]]; then
    case "${BACKUP_MIRROR_DIRECTORY}" in
        /*) ;;
        *) die "BACKUP_MIRROR_DIRECTORY должен быть абсолютным путём." ;;
    esac
    [[ "${BACKUP_MIRROR_DIRECTORY}" != "/" ]] || die "Корневой каталог нельзя использовать как mirror."
    if [[ "${BACKUP_MIRROR_REQUIRE_MOUNT}" == "true" ]] && \
        ! mountpoint -q "${BACKUP_MIRROR_DIRECTORY}"; then
        die "Backup mirror не смонтирован: ${BACKUP_MIRROR_DIRECTORY}"
    fi
    install -d -m 0700 "${BACKUP_MIRROR_DIRECTORY}"
    install -m 0600 "${final_path}" "${BACKUP_MIRROR_DIRECTORY}/${filename}.gz"
fi

if [[ -n "${RESTIC_REPOSITORY}" ]]; then
    command -v restic >/dev/null 2>&1 || die "Настроен RESTIC_REPOSITORY, но restic не установлен."
    [[ -n "${RESTIC_PASSWORD_FILE}" && -r "${RESTIC_PASSWORD_FILE}" ]] || \
        die "RESTIC_PASSWORD_FILE не настроен или недоступен."
    export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
    restic backup --tag call-you-bot -- "${final_path}"
    restic forget --tag call-you-bot \
        --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
fi

find "${BACKUP_DIRECTORY}" -maxdepth 1 -type f \
    -name 'bot-*.sqlite3.gz' -mtime "+${BACKUP_RETENTION_DAYS}" -delete

ping_heartbeat "${BACKUP_HEARTBEAT_URL}"
log "Backup создан и проверен: ${final_path}"
