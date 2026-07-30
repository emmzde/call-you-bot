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

AUTOMATIC=0
if [[ "${1:-}" == "--automatic" ]]; then
    AUTOMATIC=1
elif [[ $# -gt 0 ]]; then
    die "Неизвестный параметр: $1"
fi

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
    git_safe merge --ff-only "${remote_revision}"
    log "Исходный код обновлён."
}

rollback_image() {
    local old_image_id="$1"
    [[ -n "${old_image_id}" ]] || return 1

    warn "Новая версия не прошла healthcheck. Возвращаю предыдущую…"
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

    update_source

    local old_container
    local old_image_id=""
    local revision
    old_container="$(bot_container_id)"
    if [[ -n "${old_container}" ]]; then
        old_image_id="$(
            docker inspect --format '{{.Image}}' "${old_container}" 2>/dev/null || true
        )"
        backup_database
    fi

    revision="$(source_revision)"
    info "Собираю новую версию; работающий бот пока не останавливается…"
    build_production_image "${revision}"
    compose up -d --force-recreate --no-build --remove-orphans bot

    info "Проверяю новую версию…"
    if ! wait_for_bot_health 180; then
        show_bot_logs
        rollback_image "${old_image_id}"
        exit 1
    fi

    docker image prune -f \
        --filter label=com.tebya-zovut-bot.managed=true >/dev/null || true
    log "Бот обновлён и работает."
}

main
