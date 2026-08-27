FROM python:3.12-slim-bookworm

ARG VCS_REF=unknown

LABEL org.opencontainers.image.source="https://github.com/emmzde/call-you-bot" \
    org.opencontainers.image.revision="${VCS_REF}" \
    com.tebya-zovut-bot.managed="true" \
    com.tebya-zovut-bot.revision="${VCS_REF}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/data/bot.sqlite3 \
    HEALTHCHECK_PATH=/tmp/tebya-zovut-bot.heartbeat \
    HEALTHCHECK_INTERVAL_SECONDS=15 \
    TELEGRAM_HEALTHCHECK_PATH=/tmp/tebya-zovut-bot.telegram.heartbeat \
    TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS=60 \
    TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS=300 \
    LOG_FORMAT=json

WORKDIR /app

COPY pyproject.toml requirements.lock README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade "pip==26.2.1" \
    && pip install --no-cache-dir --requirement requirements.lock \
    && pip install --no-cache-dir --no-deps . \
    && python -m pip uninstall --yes pip setuptools wheel \
    && useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin bot \
    && mkdir -p /data \
    && chown bot:bot /data

USER 10001:10001

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "tebya_zovut_bot.healthcheck"]

CMD ["tebya-zovut-bot"]
