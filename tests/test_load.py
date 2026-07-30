import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.types import Chat, Message, MessageEntity, User

from tebya_zovut_bot.handlers import create_router
from tebya_zovut_bot.notifier import NotificationWorker
from tebya_zovut_bot.storage import Storage


class YieldingFakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        self.sent.append(kwargs)


def test_processes_mentions_for_300_registered_users(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "load.sqlite3")
    try:
        bot = YieldingFakeBot()
        router = create_router(
            storage=storage,
            bot_id=999,
            bot_username="tebya_zovut_bot",
        )
        group_handler = router.message.handlers[-1].callback
        author = User(
            id=9000,
            is_bot=False,
            first_name="Автор",
            username="release_author",
        )
        messages: list[Message] = []

        for index in range(300):
            username = f"user{index:04d}"
            storage.remember_user(
                user_id=10_000 + index,
                username=username,
                first_name=f"User {index}",
                dm_allowed=True,
            )
            text = f"@{username} проверьте релиз"
            messages.append(
                Message(
                    message_id=index + 1,
                    date=datetime.now(UTC),
                    chat=Chat(
                        id=-100987654321 - index,
                        type="supergroup",
                        title=f"Релиз {index}",
                    ),
                    from_user=author,
                    text=text,
                    entities=[
                        MessageEntity(
                            type="mention",
                            offset=0,
                            length=len(username) + 1,
                        )
                    ],
                )
            )

        async def process_all() -> None:
            await asyncio.gather(*(group_handler(message, bot) for message in messages))
            delivery_worker = NotificationWorker(
                bot=bot,
                storage=storage,
                send_rate_per_second=0,
            )
            while await delivery_worker.process_next():
                pass

        asyncio.run(process_all())

        assert len(bot.sent) == 300
        assert {item["chat_id"] for item in bot.sent} == {
            10_000 + index for index in range(300)
        }
        assert storage.queue_stats().sent == 300
    finally:
        storage.close()
