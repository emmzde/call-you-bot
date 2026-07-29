from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from .message_tools import (
    UnsupportedMessageLink,
    build_message_link,
    extract_mentions,
    notification_context,
    short_quote,
)
from .storage import Storage

LOGGER = logging.getLogger(__name__)
GROUP_TYPES = {"group", "supergroup"}
ACTIVE_MEMBER_STATUSES = {"member", "administrator", "creator"}
INACTIVE_MEMBER_STATUSES = {"left", "kicked"}

NOTIFICATION_TEXT = "Тебя зовут! Давай быстрее;)"
GROUP_WELCOME_TEXT = (
    "Бот «Тебя зовут!» подключён.\n\n"
    "Каждому участнику нужно один раз открыть бота по кнопке ниже и нажать "
    "Start — Telegram запрещает ботам первыми начинать личный диалог.\n"
    "Это тестовый экземпляр, не судите строго. By hiraeth"
)

CONTENT_TITLES = {
    "animation": "Анимация",
    "audio": "Аудио",
    "contact": "Контакт",
    "dice": "Сообщение с кубиком",
    "document": "Документ",
    "location": "Геопозиция",
    "photo": "Фотография",
    "poll": "Опрос",
    "sticker": "Стикер",
    "story": "История",
    "venue": "Место",
    "video": "Видео",
    "video_note": "Видеосообщение",
    "voice": "Голосовое сообщение",
}


class OutboundRateLimiter:
    """Serialize Bot API sends at a conservative global rate."""

    def __init__(self, rate_per_second: float) -> None:
        if rate_per_second < 0:
            raise ValueError("rate_per_second cannot be negative")
        self._interval = 0.0 if rate_per_second == 0 else 1.0 / rate_per_second
        self._lock: asyncio.Lock | None = None
        self._next_slot = 0.0

    async def wait(self) -> None:
        if self._interval == 0:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_slot - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_slot = max(self._next_slot, loop.time()) + self._interval


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _remember(storage: Storage, user: User | None, *, private: bool) -> None:
    if user is None or user.is_bot:
        return
    storage.remember_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        dm_allowed=private,
    )


def _registration_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Зарегистрироваться в боте",
                    url=f"https://t.me/{bot_username}?start=register",
                )
            ]
        ]
    )


def _private_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Добавить бота в рабочий чат",
                    url=f"https://t.me/{bot_username}?startgroup=setup",
                )
            ]
        ]
    )


async def _send_notification(
    bot: Bot,
    *,
    user_id: int,
    context: str,
    message_link: str,
    rate_limiter: OutboundRateLimiter,
) -> None:
    await rate_limiter.wait()
    await _send_message_with_retry(bot, chat_id=user_id, text=NOTIFICATION_TEXT)
    await rate_limiter.wait()
    await _send_message_with_retry(
        bot,
        chat_id=user_id,
        text=context,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↗ Перейти к сообщению",
                        url=message_link,
                    )
                ]
            ]
        ),
    )


async def _send_message_with_retry(
    bot: Bot,
    *,
    attempts: int = 4,
    **kwargs: Any,
) -> Message:
    if attempts < 1:
        raise ValueError("attempts must be positive")

    for attempt in range(1, attempts + 1):
        try:
            return await bot.send_message(**kwargs)
        except TelegramRetryAfter as error:
            if attempt == attempts:
                raise
            delay = max(float(error.retry_after), 0.0) + 0.1
            LOGGER.warning(
                "Telegram flood control; retrying in %.1fs (%s/%s)",
                delay,
                attempt,
                attempts,
            )
            await asyncio.sleep(delay)
        except (TelegramNetworkError, TelegramServerError):
            if attempt == attempts:
                raise
            delay = min(0.5 * (2 ** (attempt - 1)), 4.0)
            LOGGER.warning(
                "Temporary Telegram failure; retrying in %.1fs (%s/%s)",
                delay,
                attempt,
                attempts,
                exc_info=True,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("unreachable")


def create_router(
    *,
    storage: Storage,
    bot_id: int,
    bot_username: str,
    send_rate_per_second: float = 25.0,
) -> Router:
    router = Router(name=__name__)
    rate_limiter = OutboundRateLimiter(send_rate_per_second)

    @router.message(CommandStart(), F.chat.type == "private")
    @router.message(Command("help"), F.chat.type == "private")
    async def private_start(message: Message) -> None:
        _remember(storage, message.from_user, private=True)
        await message.answer(
            "Готово! Теперь, когда вас упомянут в подключённом рабочем чате, "
            "я пришлю сюда уведомление и кнопку перехода к сообщению.",
            reply_markup=_private_keyboard(bot_username),
        )

    @router.message(F.chat.type == "private")
    async def private_fallback(message: Message) -> None:
        _remember(storage, message.from_user, private=True)
        await message.answer(
            "Я на связи. Упоминания из подключённых рабочих чатов будут приходить сюда."
        )

    @router.message(Command("setup"))
    async def group_setup(message: Message) -> None:
        if _enum_value(message.chat.type) not in GROUP_TYPES:
            return
        _remember(storage, message.from_user, private=False)
        await message.answer(
            GROUP_WELCOME_TEXT,
            reply_markup=_registration_keyboard(bot_username),
        )

    @router.my_chat_member()
    async def bot_membership_changed(event: ChatMemberUpdated, bot: Bot) -> None:
        if _enum_value(event.chat.type) not in GROUP_TYPES:
            return

        old_status = _enum_value(event.old_chat_member.status)
        new_status = _enum_value(event.new_chat_member.status)
        if (
            old_status in INACTIVE_MEMBER_STATUSES
            and new_status in ACTIVE_MEMBER_STATUSES
        ):
            LOGGER.info(
                "Bot added to chat id=%s title=%r type=%s status=%s",
                event.chat.id,
                event.chat.title,
                _enum_value(event.chat.type),
                new_status,
            )
            await bot.send_message(
                chat_id=event.chat.id,
                text=GROUP_WELCOME_TEXT,
                reply_markup=_registration_keyboard(bot_username),
            )

    async def process_group_message(message: Message, bot: Bot) -> None:
        if _enum_value(message.chat.type) not in GROUP_TYPES:
            return

        _remember(storage, message.from_user, private=False)
        source_text = message.text if message.text is not None else message.caption
        entities = (
            message.entities if message.text is not None else message.caption_entities
        )
        mentions = extract_mentions(source_text, entities)
        if not mentions:
            return
        LOGGER.info(
            "Mention detected in chat id=%s title=%r type=%s message=%s",
            message.chat.id,
            message.chat.title,
            _enum_value(message.chat.type),
            message.message_id,
        )

        try:
            message_link = build_message_link(
                chat_id=message.chat.id,
                chat_username=message.chat.username,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                single=message.media_group_id is not None,
            )
        except UnsupportedMessageLink:
            LOGGER.warning(
                "Cannot create an exact message link for basic group %s",
                message.chat.id,
            )
            return

        target_ids: set[int] = set()
        for mention in mentions:
            if mention.user is not None:
                _remember(storage, mention.user, private=False)
                if not mention.user.is_bot:
                    target_ids.add(mention.user.id)
                continue

            if mention.username is not None:
                resolved_id = storage.resolve_username(mention.username)
                if resolved_id is not None:
                    target_ids.add(resolved_id)
                else:
                    LOGGER.info(
                        "Mention @%s is not registered; notification skipped",
                        mention.username,
                    )

        target_ids.discard(bot_id)
        if not target_ids:
            return

        fallback = CONTENT_TITLES.get(
            _enum_value(message.content_type), "Новое сообщение"
        )
        quote_text = short_quote(source_text, fallback=fallback)
        context = notification_context(quote_text)

        for user_id in target_ids:
            if not storage.claim_notification(
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=user_id,
            ):
                continue

            try:
                await _send_notification(
                    bot,
                    user_id=user_id,
                    context=context,
                    message_link=message_link,
                    rate_limiter=rate_limiter,
                )
            except (TelegramForbiddenError, TelegramBadRequest) as error:
                storage.set_dm_allowed(user_id, False)
                storage.finish_notification(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=user_id,
                    sent=False,
                    error=str(error),
                )
                LOGGER.info(
                    "Cannot notify user %s; they may not have started the bot",
                    user_id,
                )
            except TelegramAPIError as error:
                storage.finish_notification(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=user_id,
                    sent=False,
                    error=str(error),
                )
                LOGGER.exception("Telegram API error while notifying %s", user_id)
            else:
                storage.set_dm_allowed(user_id, True)
                storage.finish_notification(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=user_id,
                    sent=True,
                )
                LOGGER.info(
                    "Notification sent to user=%s for chat=%s message=%s",
                    user_id,
                    message.chat.id,
                    message.message_id,
                )

    router.message.register(process_group_message)
    router.edited_message.register(process_group_message)
    return router
