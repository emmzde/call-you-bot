from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _as_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, "").strip()
    value = default if not raw else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _as_float(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name, "").strip()
    value = default if not raw else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _as_user_ids(value: str | None) -> frozenset[int]:
    if not value or not value.strip():
        return frozenset()
    result: set[int] = set()
    for item in value.split(","):
        user_id = int(item.strip())
        if user_id <= 0:
            raise ValueError("ADMIN_USER_IDS must contain positive integers")
        result.add(user_id)
    return frozenset(result)


def _read_bot_token() -> str:
    token = os.getenv("BOT_TOKEN", "").strip()
    token_file = os.getenv("BOT_TOKEN_FILE", "").strip()
    if token and token_file:
        raise RuntimeError("Set only one of BOT_TOKEN or BOT_TOKEN_FILE")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError(f"Cannot read BOT_TOKEN_FILE: {token_file}") from error
    if not token:
        raise RuntimeError(
            "BOT_TOKEN or BOT_TOKEN_FILE is required. Use the token issued "
            "by @BotFather."
        )
    if any(character.isspace() for character in token):
        raise RuntimeError("The bot token contains whitespace")
    return token


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    database_path: Path
    log_level: str
    log_format: str
    drop_pending_updates: bool
    send_rate_per_second: float
    polling_timeout_seconds: int
    notification_retry_base_seconds: float
    notification_retry_max_seconds: float
    notification_max_attempts: int
    notification_retention_days: int
    maintenance_interval_seconds: int
    shutdown_grace_seconds: int
    healthcheck_path: Path
    healthcheck_interval_seconds: int
    watchdog_timeout_seconds: int
    telegram_healthcheck_path: Path
    telegram_healthcheck_interval_seconds: int
    telegram_healthcheck_max_age_seconds: int
    admin_user_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> Config:
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"Unsupported LOG_LEVEL: {log_level!r}")

        log_format = os.getenv("LOG_FORMAT", "text").strip().lower()
        if log_format not in {"text", "json"}:
            raise ValueError("LOG_FORMAT must be either 'text' or 'json'")

        retry_base = _as_float(
            "NOTIFICATION_RETRY_BASE_SECONDS",
            default=2.0,
            minimum=0.1,
            maximum=300.0,
        )
        retry_max = _as_float(
            "NOTIFICATION_RETRY_MAX_SECONDS",
            default=300.0,
            minimum=1.0,
            maximum=3600.0,
        )
        if retry_base > retry_max:
            raise ValueError(
                "NOTIFICATION_RETRY_BASE_SECONDS cannot exceed "
                "NOTIFICATION_RETRY_MAX_SECONDS"
            )

        health_interval = _as_int(
            "HEALTHCHECK_INTERVAL_SECONDS",
            default=15,
            minimum=5,
            maximum=300,
        )
        watchdog_timeout = _as_int(
            "WATCHDOG_TIMEOUT_SECONDS",
            default=120,
            minimum=30,
            maximum=3600,
        )
        if watchdog_timeout < health_interval * 3:
            raise ValueError(
                "WATCHDOG_TIMEOUT_SECONDS must be at least three times "
                "HEALTHCHECK_INTERVAL_SECONDS"
            )

        telegram_health_interval = _as_int(
            "TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS",
            default=60,
            minimum=15,
            maximum=600,
        )
        telegram_health_max_age = _as_int(
            "TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS",
            default=300,
            minimum=60,
            maximum=3600,
        )
        if telegram_health_max_age < telegram_health_interval * 3:
            raise ValueError(
                "TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS must be at least three "
                "times TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS"
            )

        return cls(
            bot_token=_read_bot_token(),
            database_path=Path(
                os.getenv("DATABASE_PATH", "data/bot.sqlite3")
            ).expanduser(),
            log_level=log_level,
            log_format=log_format,
            drop_pending_updates=_as_bool(
                os.getenv("DROP_PENDING_UPDATES"),
                default=False,
            ),
            send_rate_per_second=_as_float(
                "SEND_RATE_PER_SECOND",
                default=25.0,
                minimum=0.1,
                maximum=29.0,
            ),
            polling_timeout_seconds=_as_int(
                "POLLING_TIMEOUT_SECONDS",
                default=30,
                minimum=5,
                maximum=50,
            ),
            notification_retry_base_seconds=retry_base,
            notification_retry_max_seconds=retry_max,
            notification_max_attempts=_as_int(
                "NOTIFICATION_MAX_ATTEMPTS",
                default=20,
                minimum=1,
                maximum=100,
            ),
            notification_retention_days=_as_int(
                "NOTIFICATION_RETENTION_DAYS",
                default=7,
                minimum=1,
                maximum=365,
            ),
            maintenance_interval_seconds=_as_int(
                "MAINTENANCE_INTERVAL_SECONDS",
                default=3600,
                minimum=60,
                maximum=86400,
            ),
            shutdown_grace_seconds=_as_int(
                "SHUTDOWN_GRACE_SECONDS",
                default=20,
                minimum=1,
                maximum=120,
            ),
            healthcheck_path=Path(
                os.getenv("HEALTHCHECK_PATH", "data/bot.heartbeat")
            ).expanduser(),
            healthcheck_interval_seconds=health_interval,
            watchdog_timeout_seconds=watchdog_timeout,
            telegram_healthcheck_path=Path(
                os.getenv(
                    "TELEGRAM_HEALTHCHECK_PATH",
                    "data/telegram.heartbeat",
                )
            ).expanduser(),
            telegram_healthcheck_interval_seconds=telegram_health_interval,
            telegram_healthcheck_max_age_seconds=telegram_health_max_age,
            admin_user_ids=_as_user_ids(os.getenv("ADMIN_USER_IDS")),
        )
