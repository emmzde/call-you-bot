FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/data/bot.sqlite3 \
    HEALTHCHECK_PATH=/tmp/tebya-zovut-bot.heartbeat \
    HEALTHCHECK_INTERVAL_SECONDS=15 \
    LOG_FORMAT=json

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && pip install --no-cache-dir . \
    && useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin bot \
    && mkdir -p /data \
    && chown bot:bot /data

USER 10001:10001

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "tebya_zovut_bot.healthcheck"]

CMD ["tebya-zovut-bot"]
