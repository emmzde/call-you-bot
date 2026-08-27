#!/usr/bin/env bash

# Shared installer/update helpers for host-level reliability services.

configure_operations() {
    local operations_directory="/etc/call-you-bot"
    local operations_file="${operations_directory}/ops.env"

    install -d -m 0700 "${operations_directory}"
    install -d -m 0700 /var/lib/call-you-bot /var/backups/call-you-bot
    if [[ ! -f "${operations_file}" ]]; then
        cat >"${operations_file}" <<'EOF'
# Root-only production reliability settings.
STATE_DIRECTORY=/var/lib/call-you-bot
UNHEALTHY_RESTART_COOLDOWN_SECONDS=900

# A missed heartbeat must alert through an external service such as Healthchecks.io.
MONITOR_HEARTBEAT_URL=
MONITOR_FAILURE_URL=
BACKUP_HEARTBEAT_URL=
BACKUP_FAILURE_URL=

# Local verified backups. Keep an off-host copy as well.
BACKUP_DIRECTORY=/var/backups/call-you-bot
BACKUP_RETENTION_DAYS=30

# Optional mounted remote disk/NFS path. It must be a real mount by default.
BACKUP_MIRROR_DIRECTORY=
BACKUP_MIRROR_REQUIRE_MOUNT=true

# Optional encrypted off-site restic repository (S3/B2/SFTP/etc.).
RESTIC_REPOSITORY=
RESTIC_PASSWORD_FILE=
EOF
    fi
    chown root:root "${operations_file}"
    chmod 0600 "${operations_file}"
    printf '%s\n' "${COMPOSE_PROJECT_NAME}" >"${operations_directory}/project-name"
    chown root:root "${operations_directory}/project-name"
    chmod 0600 "${operations_directory}/project-name"
    log "Production-настройки надёжности подготовлены: ${operations_file}"
}

install_reliability_timers() {
    local escaped_project
    escaped_project="${PROJECT_DIR//\\/\\\\}"
    escaped_project="${escaped_project//\"/\\\"}"
    escaped_project="${escaped_project//%/%%}"

    cat >/etc/systemd/system/call-you-bot-reconcile.service <<EOF
[Unit]
Description=Reconcile and self-heal call-you-bot
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="${escaped_project}"
ExecStart=/bin/bash "${escaped_project}/deploy/reconcile.sh"
TimeoutStartSec=5min
EOF

    cat >/etc/systemd/system/call-you-bot-reconcile.timer <<'EOF'
[Unit]
Description=Check call-you-bot health every minute

[Timer]
OnBootSec=45s
OnUnitActiveSec=60s
AccuracySec=10s
RandomizedDelaySec=5s
Unit=call-you-bot-reconcile.service

[Install]
WantedBy=timers.target
EOF

    cat >/etc/systemd/system/call-you-bot-backup.service <<EOF
[Unit]
Description=Create and export a verified call-you-bot database backup
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="${escaped_project}"
ExecStart=/bin/bash "${escaped_project}/deploy/backup.sh"
TimeoutStartSec=2h
Nice=10
IOSchedulingClass=idle
EOF

    cat >/etc/systemd/system/call-you-bot-backup.timer <<'EOF'
[Unit]
Description=Daily verified call-you-bot database backup

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=30m
Persistent=true
Unit=call-you-bot-backup.service

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now \
        call-you-bot-reconcile.timer \
        call-you-bot-backup.timer
    log "Самовосстановление и ежедневные backups включены."
}
