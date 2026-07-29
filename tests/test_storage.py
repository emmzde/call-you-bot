from pathlib import Path

from tebya_zovut_bot.storage import Storage


def test_usernames_are_resolved_case_insensitively(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=10,
            username="ReleaseLead",
            first_name="Анна",
            dm_allowed=True,
        )

        assert storage.resolve_username("@releaselead") == 10
        assert storage.resolve_username("RELEASELEAD") == 10
    finally:
        storage.close()


def test_username_is_reassigned_to_latest_account(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=10,
            username="releaselead",
            first_name="Анна",
        )
        storage.remember_user(
            user_id=20,
            username="ReleaseLead",
            first_name="Иван",
        )

        assert storage.resolve_username("releaselead") == 20
    finally:
        storage.close()


def test_notification_can_only_be_claimed_once(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        first = storage.claim_notification(chat_id=-1001, message_id=7, user_id=2)
        second = storage.claim_notification(chat_id=-1001, message_id=7, user_id=2)

        assert first is True
        assert second is False
    finally:
        storage.close()
