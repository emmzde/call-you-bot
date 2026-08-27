#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

if ((EUID != 0)); then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'Нужны права root. Установите sudo или войдите как root.\n' >&2
        exit 1
    fi
    exec sudo /bin/bash "${SCRIPT_PATH}" "$@"
fi

# shellcheck source=deploy/common.sh
source "$(dirname -- "${SCRIPT_PATH}")/common.sh"
# shellcheck source=deploy/reliability.sh
source "$(dirname -- "${SCRIPT_PATH}")/reliability.sh"

AUTO_UPDATES=1
RECONFIGURE_TOKEN=0
OLD_IMAGE_ID=""
OLD_SOURCE_REVISION=""
OLD_TELEGRAM_HEALTHY=0
PRE_SWITCH_BACKUP_CREATED=0
REPLACEMENT_ATTEMPTED=0
TOKEN_REPLACED=0
TOKEN_ROLLBACK_FILE=""
TOKEN_STAGE_FILE=""

for argument in "$@"; do
    case "${argument}" in
        --no-auto-updates)
            AUTO_UPDATES=0
            ;;
        --reconfigure-token)
            RECONFIGURE_TOKEN=1
            ;;
        *)
            die "Неизвестный параметр: ${argument}"
            ;;
    esac
done

cleanup_install() {
    if [[ -n "${TOKEN_ROLLBACK_FILE}" ]]; then
        rm -f -- "${TOKEN_ROLLBACK_FILE}"
    fi
    if [[ -n "${TOKEN_STAGE_FILE}" ]]; then
        rm -f -- "${TOKEN_STAGE_FILE}"
    fi
}

rollback_existing_installation() {
    local restored_token=0

    if ((TOKEN_REPLACED == 1)) && [[ -s "${TOKEN_ROLLBACK_FILE}" ]]; then
        install -m 0600 "${TOKEN_ROLLBACK_FILE}" \
            "${PROJECT_DIR}/secrets/bot_token"
        TOKEN_REPLACED=0
        restored_token=1
        warn "Предыдущий Telegram token восстановлен."
    fi

    if ((REPLACEMENT_ATTEMPTED == 0)) || [[ -z "${OLD_IMAGE_ID}" ]]; then
        return 0
    fi

    warn "Возвращаю предыдущий production-образ…"
    if [[ -n "${OLD_SOURCE_REVISION}" ]]; then
        git_safe reset --hard "${OLD_SOURCE_REVISION}"
        warn "Production-файлы возвращены к revision ${OLD_SOURCE_REVISION}."
    fi
    docker tag "${OLD_IMAGE_ID}" "${BOT_IMAGE}"
    compose up -d --force-recreate --no-build --remove-orphans bot
    REPLACEMENT_ATTEMPTED=0
    if wait_for_bot_health 180; then
        log "Предыдущая рабочая версия восстановлена."
        return 0
    fi
    show_bot_logs
    if ((restored_token == 1)); then
        warn "Старый token возвращён, но контейнер не стал healthy."
    fi
    return 1
}

on_error() {
    local exit_code=$?
    local failed_line="${BASH_LINENO[0]}"
    trap - ERR
    if ! rollback_existing_installation; then
        warn "Автоматический rollback не завершился; требуется администратор."
    fi
    printf '\n\033[1;31mУстановка остановлена на строке %s.\033[0m\n' \
        "${failed_line}" >&2
    printf 'Скопируйте этот вывод разработчику. Код ошибки: %s\n' \
        "${exit_code}" >&2
    exit "${exit_code}"
}
trap on_error ERR
trap cleanup_install EXIT

load_os_release() {
    [[ -r /etc/os-release ]] || die "Не удалось определить Linux-дистрибутив."
    # The file is controlled by the operating system.
    # shellcheck disable=SC1091
    source /etc/os-release
}

install_basic_tools() {
    load_os_release
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y \
            ca-certificates coreutils curl findutils git gzip util-linux
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y \
            ca-certificates coreutils curl findutils git gzip util-linux
    else
        die "Поддерживаются Ubuntu, Debian, Fedora, RHEL, Rocky, AlmaLinux и CentOS."
    fi
}

install_docker_apt() {
    local docker_os
    local suite
    local architecture

    case "${ID}" in
        ubuntu)
            docker_os="ubuntu"
            suite="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
            ;;
        debian)
            docker_os="debian"
            suite="${VERSION_CODENAME:-}"
            ;;
        *)
            if [[ " ${ID_LIKE:-} " == *" ubuntu "* ]]; then
                docker_os="ubuntu"
                suite="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
            elif [[ " ${ID_LIKE:-} " == *" debian "* ]]; then
                docker_os="debian"
                suite="${VERSION_CODENAME:-}"
            else
                die "Этот apt-дистрибутив пока не поддерживается: ${ID}"
            fi
            ;;
    esac
    [[ -n "${suite}" ]] || die "Не удалось определить codename дистрибутива."

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${docker_os}/gpg" \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    architecture="$(dpkg --print-architecture)"

    cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${docker_os}
Suites: ${suite}
Components: stable
Architectures: ${architecture}
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
}

install_docker_dnf() {
    local repository_os
    case "${ID}" in
        fedora)
            repository_os="fedora"
            ;;
        rhel)
            repository_os="rhel"
            ;;
        centos | rocky | almalinux)
            repository_os="centos"
            ;;
        *)
            if [[ " ${ID_LIKE:-} " == *" fedora "* ]]; then
                repository_os="fedora"
            elif [[ " ${ID_LIKE:-} " == *" rhel "* ]]; then
                repository_os="centos"
            else
                die "Этот dnf-дистрибутив пока не поддерживается: ${ID}"
            fi
            ;;
    esac

    curl -fsSL "https://download.docker.com/linux/${repository_os}/docker-ce.repo" \
        -o /etc/yum.repos.d/docker-ce.repo
    dnf install -y \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1 &&
        docker compose version >/dev/null 2>&1; then
        info "Docker и Compose уже установлены."
    else
        info "Устанавливаю Docker Engine и Compose из официального репозитория…"
        load_os_release
        if command -v apt-get >/dev/null 2>&1; then
            install_docker_apt
        elif command -v dnf >/dev/null 2>&1; then
            install_docker_dnf
        else
            die "На этом сервере нет поддерживаемого пакетного менеджера."
        fi
    fi

    command -v systemctl >/dev/null 2>&1 ||
        die "Для production-запуска требуется systemd."
    systemctl enable --now docker
    docker info >/dev/null
    docker compose version
    log "Docker запущен и добавлен в автозагрузку."
}

configure_update_source() {
    local repository_url="${BOT_REPOSITORY_URL:-https://github.com/emmzde/call-you-bot.git}"
    local repository_branch="${BOT_REPOSITORY_BRANCH:-main}"

    if git_safe rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return
    fi

    info "Настраиваю безопасное получение будущих обновлений…"
    git -C "${PROJECT_DIR}" init -b "${repository_branch}"
    git_safe remote add origin "${repository_url}"
    if ! git_safe fetch origin "${repository_branch}"; then
        warn "Репозиторий пока недоступен — бот установится без автообновлений."
        return
    fi

    # This only creates Git metadata for an unpacked archive. Working files are
    # deliberately preserved; differences remain visible and block auto-update.
    git_safe reset --mixed "origin/${repository_branch}"
    git_safe branch --set-upstream-to="origin/${repository_branch}" \
        "${repository_branch}"

    if [[ -n "$(git_safe status --porcelain)" ]]; then
        warn "Файлы отличаются от опубликованной версии; автообновление не будет их менять."
    else
        log "Источник автоматических обновлений настроен."
    fi
}

configure_environment() {
    local environment_file="${PROJECT_DIR}/.env"
    if [[ ! -f "${environment_file}" ]]; then
        cat >"${environment_file}" <<'EOF'
DATABASE_PATH=/data/bot.sqlite3
LOG_LEVEL=INFO
LOG_FORMAT=json
DROP_PENDING_UPDATES=false
SEND_RATE_PER_SECOND=25
POLLING_TIMEOUT_SECONDS=30
NOTIFICATION_RETRY_BASE_SECONDS=2
NOTIFICATION_RETRY_MAX_SECONDS=300
NOTIFICATION_MAX_ATTEMPTS=20
NOTIFICATION_RETENTION_DAYS=7
MAINTENANCE_INTERVAL_SECONDS=3600
SHUTDOWN_GRACE_SECONDS=20
HEALTHCHECK_INTERVAL_SECONDS=15
WATCHDOG_TIMEOUT_SECONDS=120
TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS=60
TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS=300
ADMIN_USER_IDS=
EOF
        log "Создан production-файл настроек."
    fi
    chmod 600 "${environment_file}"
}

configure_token() {
    local secret_directory="${PROJECT_DIR}/secrets"
    local token_file="${secret_directory}/bot_token"
    local token=""

    install -d -m 0700 "${secret_directory}"
    if [[ -s "${token_file}" && "${RECONFIGURE_TOKEN}" -eq 0 ]]; then
        chmod 600 "${token_file}"
        info "Токен уже настроен, оставляю его без изменений."
        return
    fi

    [[ -r /dev/tty ]] ||
        die "Для первого запуска нужен интерактивный терминал для ввода токена."
    printf '\nВставьте токен бота от @BotFather (ввод будет скрыт): ' >/dev/tty
    IFS= read -r -s token </dev/tty
    printf '\n' >/dev/tty

    if [[ ! "${token}" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]]; then
        unset token
        die "Токен имеет неверный формат. Запустите установку ещё раз."
    fi
    if [[ -s "${token_file}" ]]; then
        TOKEN_ROLLBACK_FILE="/var/lib/call-you-bot/token-rollback-$$"
        install -m 0600 "${token_file}" "${TOKEN_ROLLBACK_FILE}"
    fi
    TOKEN_STAGE_FILE="${secret_directory}/.bot_token-$$"
    printf '%s' "${token}" >"${TOKEN_STAGE_FILE}"
    chmod 600 "${TOKEN_STAGE_FILE}"
    TOKEN_REPLACED=1
    mv -f -- "${TOKEN_STAGE_FILE}" "${token_file}"
    TOKEN_STAGE_FILE=""
    unset token
    log "Токен сохранён как Docker secret."
}

install_update_timer() {
    local escaped_project
    escaped_project="${PROJECT_DIR//\\/\\\\}"
    escaped_project="${escaped_project//\"/\\\"}"
    escaped_project="${escaped_project//%/%%}"

    if ((AUTO_UPDATES == 0)); then
        systemctl disable --now call-you-bot-update.timer >/dev/null 2>&1 || true
        warn "Автообновления отключены параметром --no-auto-updates."
        return
    fi
    if ! git_safe rev-parse HEAD >/dev/null 2>&1 ||
        ! git_safe remote get-url origin >/dev/null 2>&1; then
        warn "Проект загружен без Git remote — автообновления кода не включены."
        return
    fi

    cat >/etc/systemd/system/call-you-bot-update.service <<EOF
[Unit]
Description=Safe automatic update for call-you-bot
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="${escaped_project}"
ExecStart=/bin/bash "${escaped_project}/deploy/update.sh" --automatic
TimeoutStartSec=45min
EOF

    cat >/etc/systemd/system/call-you-bot-update.timer <<'EOF'
[Unit]
Description=Daily safe update check for call-you-bot

[Timer]
OnCalendar=*-*-* 04:15:00
RandomizedDelaySec=30m
Persistent=true
Unit=call-you-bot-update.service

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now call-you-bot-update.timer
    log "Безопасная проверка обновлений включена раз в сутки."
}

main() {
    cd "${PROJECT_DIR}"
    info "Подготавливаю сервер для бота «Тебя зовут!»…"
    install_basic_tools
    ensure_docker
    configure_update_source
    configure_environment
    configure_operations
    acquire_operations_lock || die "Другая production-операция уже выполняется."

    local candidate_old_revision=""
    local old_container
    local old_status=""
    old_container="$(bot_container_id)"
    if [[ -n "${old_container}" ]]; then
        OLD_IMAGE_ID="$(
            docker inspect --format '{{.Image}}' "${old_container}" 2>/dev/null || true
        )"
        candidate_old_revision="$(
            docker inspect --format \
                '{{index .Config.Labels "com.tebya-zovut-bot.revision"}}' \
                "${old_container}" 2>/dev/null || true
        )"
        if [[ "${candidate_old_revision}" =~ ^[0-9a-f]{40,64}$ ]] && \
            [[ -z "$(git_safe status --porcelain 2>/dev/null || true)" ]] && \
            git_safe cat-file -e "${candidate_old_revision}^{commit}" 2>/dev/null; then
            OLD_SOURCE_REVISION="${candidate_old_revision}"
        fi
        old_status="$(
            docker inspect --format '{{.State.Status}}' \
                "${old_container}" 2>/dev/null || true
        )"
        if [[ "${old_status}" == "running" ]]; then
            if telegram_is_healthy; then
                OLD_TELEGRAM_HEALTHY=1
            fi
            info "Создаю backup перед переключением существующей установки…"
            /bin/bash "${PROJECT_DIR}/deploy/backup.sh"
            PRE_SWITCH_BACKUP_CREATED=1
        fi
    fi

    configure_token

    local revision
    revision="$(source_revision)"
    info "Подготавливаю production-образ…"
    prepare_production_image "${revision}"
    REPLACEMENT_ATTEMPTED=1
    if ! compose up -d --no-build --remove-orphans bot; then
        rollback_existing_installation || true
        die "Не удалось запустить новый production-контейнер."
    fi

    info "Жду, пока бот пройдёт самопроверку…"
    if ! wait_for_bot_health 180; then
        show_bot_logs
        if rollback_existing_installation; then
            die "Новая версия не прошла healthcheck; предыдущая восстановлена."
        fi
        die "Бот не прошёл healthcheck. Данные сохранены, но требуется администратор."
    fi

    telegram_ready=1
    info "Проверяю реальную связь с Telegram API…"
    if ! wait_for_telegram_health 180; then
        telegram_ready=0
        if [[ -n "${OLD_IMAGE_ID}" ]] && \
            ((OLD_TELEGRAM_HEALTHY == 1 || TOKEN_REPLACED == 1)); then
            rollback_existing_installation || true
            die "Новая конфигурация не подтвердила Telegram; оставлена предыдущая версия."
        fi
        warn "Telegram API пока недоступен; бот продолжит переподключаться без restart-loop."
    fi
    REPLACEMENT_ATTEMPTED=0

    if ((PRE_SWITCH_BACKUP_CREATED == 0)); then
        info "Создаю первый внешний backup базы…"
        /bin/bash "${PROJECT_DIR}/deploy/backup.sh"
    fi

    install_update_timer
    install_reliability_timers
    docker image prune -f \
        --filter label=com.tebya-zovut-bot.managed=true >/dev/null || true

    printf '\n'
    log "Установка полностью завершена."
    if ((telegram_ready == 1)); then
        printf 'Бот работает и будет автоматически запускаться после перезагрузки.\n'
    else
        warn "Сервис установлен, но связь с Telegram ещё не подтверждена."
    fi
    printf 'Проверить состояние: sudo bash "%s/deploy/status.sh"\n' "${PROJECT_DIR}"
    printf 'Обновить вручную:  sudo bash "%s/deploy/update.sh"\n' "${PROJECT_DIR}"
}

main
