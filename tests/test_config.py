from pathlib import Path

import pytest

from tebya_zovut_bot.config import Config


def test_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_PATH", "custom/bot.sqlite3")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DROP_PENDING_UPDATES", "yes")

    config = Config.from_env()

    assert config.bot_token == "123:test"
    assert config.database_path == Path("custom/bot.sqlite3")
    assert config.log_level == "DEBUG"
    assert config.drop_pending_updates is True


def test_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)

    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        Config.from_env()


def test_config_reads_token_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "bot_token"
    token_file.write_text("123:file-token\n", encoding="utf-8")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("BOT_TOKEN_FILE", str(token_file))

    assert Config.from_env().bot_token == "123:file-token"
