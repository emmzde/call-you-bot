import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, MessageEntity, User

from tebya_zovut_bot.handlers import (
    NOTIFICATION_TEXT,
    _send_message_with_retry,
    create_router,
)
from tebya_zovut_bot.notifier import NotificationWorker
from tebya_zovut_bot.storage import Storage


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Message | None:
        self.sent.append(kwargs)
        return None


def test_group_mention_sends_one_durable_notification_once(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=200,
            username="ReleaseLead",
            first_name="Анна",
            dm_allowed=True,
        )
        bot = FakeBot()
        worker = NotificationWorker(
            bot=bot,
            storage=storage,
            send_rate_per_second=0,
        )
        router = create_router(
            storage=storage,
            bot_id=999,
            bot_username="tebya_zovut_bot",
            notification_worker=worker,
        )
        group_handler = router.message.handlers[-1].callback
        source = "Релиз готов, зовём @ReleaseLead"
        username = "@ReleaseLead"
        offset = len(source[: source.index(username)].encode("utf-16-le")) // 2
        message = Message(
            message_id=77,
            date=datetime.now(UTC),
            chat=Chat(
                id=-100987654321,
                type="supergroup",
                title="Релизы",
            ),
            from_user=User(
                id=100,
                is_bot=False,
                first_name="Иван",
                username="Author",
            ),
            text=source,
            entities=[
                MessageEntity(
                    type="mention",
                    offset=offset,
                    length=len(username),
                )
            ],
        )

        asyncio.run(group_handler(message, bot))
        asyncio.run(group_handler(message, bot))
        asyncio.run(worker.process_next())

        assert len(bot.sent) == 1
        assert bot.sent[0]["chat_id"] == 200
        assert bot.sent[0]["text"] == (
            f"{NOTIFICATION_TEXT}\n\nТекст сообщения: {source}"
        )
        button = bot.sent[0]["reply_markup"].inline_keyboard[0][0]
        assert button.text == "Релизы"
        assert button.url == "https://t.me/c/987654321/77"
        assert storage.queue_stats().sent == 1
    finally:
        storage.close()


def test_send_retries_after_telegram_flood_control() -> None:
    class RateLimitedBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send_message(self, **kwargs: Any) -> Message | None:
            self.calls += 1
            if self.calls == 1:
                raise TelegramRetryAfter(
                    method=SendMessage(chat_id=200, text="test"),
                    message="retry",
                    retry_after=0,
                )
            return await super().send_message(**kwargs)

    bot = RateLimitedBot()

    asyncio.run(_send_message_with_retry(bot, chat_id=200, text="test", attempts=2))

    assert bot.calls == 2
    assert bot.sent == [{"chat_id": 200, "text": "test"}]
