from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    database_path: Path
    log_level: str
    drop_pending_updates: bool

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Copy .env.example to .env and add the "
                "token issued by @BotFather."
            )

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"Unsupported LOG_LEVEL: {log_level!r}")

        return cls(
            bot_token=token,
            database_path=Path(
                os.getenv("DATABASE_PATH", "data/bot.sqlite3")
            ).expanduser(),
            log_level=log_level,
            drop_pending_updates=_as_bool(
                os.getenv("DROP_PENDING_UPDATES"), default=False
            ),
        )
