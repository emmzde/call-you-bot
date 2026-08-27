#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

REPOSITORY_URL="${BOT_REPOSITORY_URL:-https://github.com/emmzde/call-you-bot.git}"
REPOSITORY_BRANCH="${BOT_REPOSITORY_BRANCH:-main}"
INSTALL_DIRECTORY="${BOT_INSTALL_DIRECTORY:-/opt/call-you-bot}"
INSTALL_ARGUMENTS=()

git_bounded() {
    git -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=60 "$@"
}

log() {
    printf '\033[1;32m[OK]\033[0m %s\n' "$*"
}

info() {
    printf '\033[1;34m[INFO]\033[0m %s\n' "$*"
}

die() {
    printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Однокомандная установка call-you-bot.

Параметры:
  --directory PATH       каталог установки (по умолчанию /opt/call-you-bot)
  --branch NAME          Git-ветка (по умолчанию main)
  --no-auto-updates      отключить ежедневные обновления
  --reconfigure-token    заменить существующий Telegram token
EOF
}

while (($#)); do
    case "$1" in
        --directory)
            (($# >= 2)) || die "После --directory нужен путь."
            INSTALL_DIRECTORY="$2"
            shift 2
            ;;
        --branch)
            (($# >= 2)) || die "После --branch нужно имя ветки."
            REPOSITORY_BRANCH="$2"
            shift 2
            ;;
        --no-auto-updates | --reconfigure-token)
            INSTALL_ARGUMENTS+=("$1")
            shift
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            die "Неизвестный параметр: $1"
            ;;
    esac
done

((EUID == 0)) || die "Запустите команду через sudo, как указано в README."
[[ "${INSTALL_DIRECTORY}" == /* ]] || die "Каталог установки должен быть абсолютным."
case "${INSTALL_DIRECTORY%/}" in
    "" | / | /bin | /boot | /dev | /etc | /home | /opt | /root | /run | /srv | /usr | /var)
        die "Выбран слишком широкий системный каталог: ${INSTALL_DIRECTORY}"
        ;;
esac
[[ "${REPOSITORY_BRANCH}" =~ ^[A-Za-z0-9._/-]+$ ]] || \
    die "Некорректное имя Git-ветки."

install_git() {
    if command -v git >/dev/null 2>&1; then
        return 0
    fi
    info "Устанавливаю Git…"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y ca-certificates git
    else
        die "Не найден поддерживаемый пакетный менеджер (apt или dnf)."
    fi
}

checkout_project() {
    local current_remote parent_directory
    parent_directory="$(dirname -- "${INSTALL_DIRECTORY}")"
    install -d -m 0755 "${parent_directory}"

    if [[ ! -e "${INSTALL_DIRECTORY}" ]]; then
        info "Скачиваю call-you-bot в ${INSTALL_DIRECTORY}…"
        git_bounded clone --branch "${REPOSITORY_BRANCH}" --single-branch \
            "${REPOSITORY_URL}" "${INSTALL_DIRECTORY}"
        return 0
    fi

    [[ -d "${INSTALL_DIRECTORY}/.git" ]] || \
        die "Каталог уже существует и не является Git-репозиторием: ${INSTALL_DIRECTORY}"
    [[ -z "$(git_bounded -C "${INSTALL_DIRECTORY}" status --porcelain)" ]] || \
        die "В каталоге установки есть локальные изменения; они не будут перезаписаны."

    current_remote="$(
        git_bounded -C "${INSTALL_DIRECTORY}" remote get-url origin 2>/dev/null || true
    )"
    [[ "${current_remote}" == "${REPOSITORY_URL}" ]] || \
        die "Origin существующего проекта отличается: ${current_remote:-none}"

    info "Обновляю существующую установку без потери настроек…"
    git_bounded -C "${INSTALL_DIRECTORY}" fetch --prune origin "${REPOSITORY_BRANCH}"
    if git_bounded -C "${INSTALL_DIRECTORY}" show-ref --verify --quiet \
        "refs/heads/${REPOSITORY_BRANCH}"; then
        git_bounded -C "${INSTALL_DIRECTORY}" checkout "${REPOSITORY_BRANCH}"
    else
        git_bounded -C "${INSTALL_DIRECTORY}" checkout -b "${REPOSITORY_BRANCH}" \
            --track "origin/${REPOSITORY_BRANCH}"
    fi
    git_bounded -C "${INSTALL_DIRECTORY}" merge --ff-only \
        "origin/${REPOSITORY_BRANCH}"
}

main() {
    install_git
    checkout_project
    log "Код готов. Передаю установку production-скрипту…"
    exec /bin/bash "${INSTALL_DIRECTORY}/deploy/install.sh" \
        "${INSTALL_ARGUMENTS[@]}"
}

main
