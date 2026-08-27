from pathlib import Path

import pytest

from tebya_zovut_bot.stress import run_stress


def test_durable_outbox_stress_profile(tmp_path: Path) -> None:
    result = run_stress(
        users=1_000,
        notifications=5_000,
        drain=1_000,
        directory=tmp_path,
    )

    assert result.queued_after_drain == 4_000
    assert result.database_bytes > 0
    assert result.total_seconds > 0


def test_stress_refuses_to_overwrite_existing_artifacts(tmp_path: Path) -> None:
    (tmp_path / "stress.sqlite3").touch()

    with pytest.raises(FileExistsError, match="existing artifacts"):
        run_stress(
            users=1,
            notifications=1,
            drain=0,
            directory=tmp_path,
        )
