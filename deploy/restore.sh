#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

if ((EUID != 0)); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Нужны права root для восстановления базы.\n' >&2
        exit 1
    fi
    exec sudo /bin/bash "${SCRIPT_PATH}" "$@"
fi

# shellcheck source=deploy/common.sh
source "$(dirname -- "${SCRIPT_PATH}")/common.sh"
load_ops_config

BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-/var/backups/call-you-bot}"
assume_yes=0
backup_argument=""
for argument in "$@"; do
    case "${argument}" in
        --yes) assume_yes=1 ;;
        -*) die "Неизвестный параметр: ${argument}" ;;
        *)
            [[ -z "${backup_argument}" ]] || die "Укажите только один backup."
            backup_argument="${argument}"
            ;;
    esac
done
[[ -n "${backup_argument}" ]] || \
    die "Использование: deploy/restore.sh BACKUP.sqlite3[.gz] [--yes]"

backup_path="$(realpath -e -- "${backup_argument}")"
[[ -f "${backup_path}" ]] || die "Backup не найден: ${backup_path}"

if ((assume_yes == 0)); then
    printf 'Будет заменена production-база данными из:\n%s\n' "${backup_path}"
    printf 'Введите RESTORE для продолжения: '
    IFS= read -r confirmation
    [[ "${confirmation}" == "RESTORE" ]] || die "Восстановление отменено."
fi

mkdir -p /run/lock
exec 9>/run/lock/call-you-bot-restore.lock
flock -n 9 || die "Другое восстановление уже выполняется."
acquire_operations_lock || die "Другая production-операция уже выполняется."

install -d -m 0700 /var/lib/call-you-bot
work_directory="$(mktemp -d /var/lib/call-you-bot/restore.XXXXXXXX)"
candidate_file="${work_directory}/candidate.sqlite3"
rollback_file="${work_directory}/rollback.sqlite3"
candidate_stage="/data/backups/.restore-candidate-$$.sqlite3"
rollback_stage="/data/backups/.restore-rollback-$$.sqlite3"
bot_stopped=0
rollback_ready=0

# Invoked indirectly by the EXIT trap.
# shellcheck disable=SC2329
cleanup() {
    if docker info >/dev/null 2>&1; then
        compose run --rm --no-deps --entrypoint rm bot \
            -f -- "${candidate_stage}" "${rollback_stage}" >/dev/null 2>&1 || true
    fi
    rm -f -- "${candidate_file}" "${rollback_file}"
    rmdir -- "${work_directory}" 2>/dev/null || true
}
trap cleanup EXIT

# Invoked indirectly by the ERR trap.
# shellcheck disable=SC2329
on_error() {
    local exit_code=$?
    trap - ERR
    if ((bot_stopped == 1 && rollback_ready == 1)); then
        warn "Restore прерван; аварийно возвращаю предыдущую базу."
        compose run --rm --no-deps --entrypoint python bot \
            -m tebya_zovut_bot.db_admin restore "${rollback_stage}" || true
        compose up -d --no-build bot || true
    elif ((bot_stopped == 1)); then
        compose up -d --no-build bot || true
    fi
    exit "${exit_code}"
}
trap on_error ERR

case "${backup_path}" in
    *.gz) gzip -dc -- "${backup_path}" >"${candidate_file}" ;;
    *) cp -- "${backup_path}" "${candidate_file}" ;;
esac
[[ -s "${candidate_file}" ]] || die "Backup пуст."

docker info >/dev/null
container_id="$(bot_container_id)"
[[ -n "${container_id}" ]] || die "Production-контейнер не найден."
[[ "$(docker inspect --format '{{.State.Status}}' "${container_id}")" == "running" ]] || \
    die "Production-контейнер должен быть запущен перед restore."

info "Сначала сохраняю текущее рабочее состояние…"
/bin/bash "${PROJECT_DIR}/deploy/backup.sh"
latest_backup="$(
    find "${BACKUP_DIRECTORY}" -maxdepth 1 -type f \
        -name 'bot-*.sqlite3.gz' -printf '%T@ %p\n' |
        sort -nr | sed -n '1{s/^[^ ]* //;p;}'
)"
[[ -n "${latest_backup}" ]] || die "Не удалось найти rollback backup."
gzip -dc -- "${latest_backup}" >"${rollback_file}"

compose exec -T bot mkdir -p /data/backups
docker cp "${candidate_file}" "${container_id}:${candidate_stage}"
docker cp "${rollback_file}" "${container_id}:${rollback_stage}"
compose exec -T bot python -m tebya_zovut_bot.db_admin check "${candidate_stage}"
compose exec -T bot python -m tebya_zovut_bot.db_admin check "${rollback_stage}"
rollback_ready=1

info "Останавливаю бот и атомарно заменяю SQLite…"
compose stop --timeout 30 bot
bot_stopped=1
compose run --rm --no-deps --entrypoint python bot \
    -m tebya_zovut_bot.db_admin restore "${candidate_stage}"
compose up -d --no-build bot
bot_stopped=0

if wait_for_bot_health 180; then
    log "База восстановлена, бот healthy."
    exit 0
fi

show_bot_logs
warn "Восстановленная база не прошла healthcheck; возвращаю предыдущую."
compose stop --timeout 30 bot || true
bot_stopped=1
compose run --rm --no-deps --entrypoint python bot \
    -m tebya_zovut_bot.db_admin restore "${rollback_stage}"
compose up -d --no-build bot
bot_stopped=0
if wait_for_bot_health 180; then
    die "Новая база отклонена; предыдущее состояние успешно возвращено."
fi
show_bot_logs
die "Не удалось автоматически вернуть предыдущую базу. Требуется администратор."
