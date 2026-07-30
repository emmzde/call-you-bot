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


def test_outbox_survives_restart_and_is_deduplicated(tmp_path: Path) -> None:
    database_path = tmp_path / "bot.sqlite3"
    storage = Storage(database_path)
    inserted = storage.enqueue_notifications(
        chat_id=-1001,
        message_id=7,
        user_ids=[2, 2],
        body_text="Notification",
        button_text="Release chat",
        message_link="https://t.me/c/1/7",
    )
    storage.close()

    reopened = Storage(database_path)
    try:
        job = reopened.next_due_notification()

        assert inserted == 1
        assert job is not None
        assert job.user_id == 2
        assert job.button_text == "Release chat"

        reopened.mark_notification_sent(job)
        assert reopened.next_due_notification() is None
        assert reopened.queue_stats().sent == 1
    finally:
        reopened.close()


def test_filters_users_who_have_not_started_the_bot(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=10,
            username="allowed",
            first_name="Allowed",
            dm_allowed=True,
        )
        storage.remember_user(
            user_id=20,
            username="not_allowed",
            first_name="Not allowed",
            dm_allowed=False,
        )

        assert storage.filter_dm_allowed([10, 20, 30]) == {10}
    finally:
        storage.close()
