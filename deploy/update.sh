#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

if ((EUID != 0)); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Нужны права root для обновления.\n' >&2
        exit 1
    fi
    exec sudo /bin/bash "${SCRIPT_PATH}" "$@"
fi

# shellcheck source=deploy/common.sh
source "$(dirname -- "${SCRIPT_PATH}")/common.sh"
# shellcheck source=deploy/reliability.sh
source "$(dirname -- "${SCRIPT_PATH}")/reliability.sh"

AUTOMATIC=0
FAILED_REVISION_FILE="${PROJECT_DIR}/.deploy/failed-revision"
TARGET_REVISION=""
PRE_UPDATE_BACKUP_CREATED=0
OLD_SOURCE_REVISION=""
if [[ "${1:-}" == "--automatic" ]]; then
    AUTOMATIC=1
elif [[ $# -gt 0 ]]; then
    die "Неизвестный параметр: $1"
fi

create_external_backup() {
    if ((PRE_UPDATE_BACKUP_CREATED == 1)) || [[ -z "$(bot_container_id)" ]]; then
        return 0
    fi
    /bin/bash "${PROJECT_DIR}/deploy/backup.sh"
    PRE_UPDATE_BACKUP_CREATED=1
}

mkdir -p /run/lock
exec 9>/run/lock/call-you-bot-update.lock
if ! flock -n 9; then
    info "Другое обновление уже выполняется."
    exit 0
fi

update_source() {
    local upstream
    local local_revision
    local remote_revision
    local running_container
    local deployed_revision=""

    if ! git_safe rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
        ! git_safe remote get-url origin >/dev/null 2>&1; then
        if ((AUTOMATIC == 1)); then
            info "Git remote отсутствует — автоматическое обновление пропущено."
            exit 0
        fi
        warn "Git remote отсутствует; пересобираю текущую локальную версию."
        return
    fi

    if [[ -n "$(git_safe status --porcelain)" ]]; then
        if ((AUTOMATIC == 1)); then
            warn "Есть локальные изменения — автообновление безопасно пропущено."
            exit 0
        fi
        die "Есть локальные изменения. Сохраните их перед обновлением."
    fi

    git_safe fetch --prune origin
    upstream="$(
        git_safe rev-parse --abbrev-ref --symbolic-full-name \
            '@{upstream}' 2>/dev/null ||
            true
    )"
    [[ -n "${upstream}" ]] || die "Для текущей ветки не настроена upstream-ветка."

    local_revision="$(git_safe rev-parse HEAD)"
    remote_revision="$(git_safe rev-parse "${upstream}")"
    TARGET_REVISION="${remote_revision}"

    if [[ -f "${FAILED_REVISION_FILE}" ]] && \
        [[ "$(<"${FAILED_REVISION_FILE}")" == "${remote_revision}" ]]; then
        if ((AUTOMATIC == 1)); then
            warn "Эта версия уже не прошла healthcheck; жду следующий revision."
            exit 0
        fi
        warn "Эта версия ранее не прошла healthcheck; повторяю по ручному запросу."
    elif [[ -f "${FAILED_REVISION_FILE}" ]]; then
        rm -f -- "${FAILED_REVISION_FILE}"
    fi
    running_container="$(bot_container_id)"
    if [[ -n "${running_container}" ]]; then
        deployed_revision="$(
            docker inspect \
                --format '{{index .Config.Labels "com.tebya-zovut-bot.revision"}}' \
                "${running_container}" 2>/dev/null || true
        )"
    fi

    if [[ "${local_revision}" == "${remote_revision}" ]]; then
        if ((AUTOMATIC == 1)) && [[ "${deployed_revision}" == "${local_revision}" ]]; then
            info "Новых версий нет."
            exit 0
        fi
        info "Код актуален; обновляю базовый образ и зависимости."
        return
    fi

    git_safe merge-base --is-ancestor \
        "${local_revision}" "${remote_revision}" ||
        die "Удалённая ветка не является fast-forward. Нужен разработчик."
    # Use the currently deployed backup code before Git replaces any files.
    create_external_backup
    git_safe merge --ff-only "${remote_revision}"
    log "Исходный код обновлён."
}

rollback_image() {
    local old_image_id="$1"
    [[ -n "${old_image_id}" ]] || return 1

    warn "Новая версия не прошла healthcheck. Возвращаю предыдущую…"
    if [[ -n "${OLD_SOURCE_REVISION}" ]]; then
        git_safe reset --hard "${OLD_SOURCE_REVISION}"
        warn "Production-файлы возвращены к revision ${OLD_SOURCE_REVISION}."
    fi
    docker tag "${old_image_id}" "${BOT_IMAGE}"
    compose up -d --force-recreate --no-build bot
    if wait_for_bot_health 180; then
        warn "Предыдущая версия восстановлена и работает."
        return 0
    fi
    show_bot_logs
    die "Автоматический откат не запустился. Требуется помощь разработчика."
}

main() {
    cd "${PROJECT_DIR}"
    command -v docker >/dev/null 2>&1 || die "Docker не установлен."
    docker info >/dev/null || die "Docker daemon недоступен."

    acquire_operations_lock || die "Другая production-операция уже выполняется."

    local old_container
    local old_image_id=""
    local candidate_old_revision=""
    local revision
    old_container="$(bot_container_id)"
    if [[ -n "${old_container}" ]]; then
        if ! telegram_is_healthy; then
            if ((AUTOMATIC == 1)); then
                warn "Telegram API недоступен — откладываю автообновление, чтобы не потерять рабочую версию."
                exit 0
            fi
            die "Telegram API недоступен. Обновление сейчас нельзя надёжно проверить."
        fi
        old_image_id="$(
            docker inspect --format '{{.Image}}' "${old_container}" 2>/dev/null || true
        )"
        candidate_old_revision="$(
            docker inspect --format \
                '{{index .Config.Labels "com.tebya-zovut-bot.revision"}}' \
                "${old_container}" 2>/dev/null || true
        )"
        if [[ "${candidate_old_revision}" =~ ^[0-9a-f]{40,64}$ ]] && \
            git_safe cat-file -e "${candidate_old_revision}^{commit}" 2>/dev/null; then
            OLD_SOURCE_REVISION="${candidate_old_revision}"
        fi
    fi

    update_source
    create_external_backup

    revision="$(source_revision)"
    info "Подготавливаю новую версию; работающий бот пока не останавливается…"
    prepare_production_image "${revision}"
    compose up -d --force-recreate --no-build --remove-orphans bot

    info "Проверяю новую версию…"
    if ! wait_for_bot_health 180; then
        show_bot_logs
        install -d -m 0700 "$(dirname -- "${FAILED_REVISION_FILE}")"
        printf '%s' "${TARGET_REVISION:-unknown}" >"${FAILED_REVISION_FILE}"
        chmod 0600 "${FAILED_REVISION_FILE}"
        rollback_image "${old_image_id}"
        exit 1
    fi
    if ! wait_for_telegram_health 180; then
        show_bot_logs
        install -d -m 0700 "$(dirname -- "${FAILED_REVISION_FILE}")"
        printf '%s' "${TARGET_REVISION:-unknown}" >"${FAILED_REVISION_FILE}"
        chmod 0600 "${FAILED_REVISION_FILE}"
        warn "Новая версия не подтвердила связь с Telegram."
        rollback_image "${old_image_id}"
        exit 1
    fi

    rm -f -- "${FAILED_REVISION_FILE}"
    configure_operations
    install_reliability_timers
    docker image prune -f \
        --filter label=com.tebya-zovut-bot.managed=true >/dev/null || true
    log "Бот обновлён и работает."
}

main
