import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.types import Chat, Message, MessageEntity, User

from tebya_zovut_bot.handlers import create_router
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
        router = create_router(
            storage=storage,
            bot_id=999,
            bot_username="tebya_zovut_bot",
            send_rate_per_second=0,
        )
        group_handler = router.message.handlers[-1].callback
        bot = YieldingFakeBot()
        author = User(
            id=9000,
            is_bot=False,
            first_name="Автор",
            username="release_author",
        )
        chat = Chat(
            id=-100987654321,
            type="supergroup",
            title="Нагрузочный тест",
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
                    chat=chat,
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

        asyncio.run(process_all())

        assert len(bot.sent) == 600
        assert {item["chat_id"] for item in bot.sent} == {
            10_000 + index for index in range(300)
        }
    finally:
        storage.close()
