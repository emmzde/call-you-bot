from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from .message_tools import (
    NOTIFICATION_TEXT as NOTIFICATION_TEXT,
    UnsupportedMessageLink,
    build_message_link,
    chat_button_label,
    extract_mentions,
    notification_context,
    notification_text,
    short_quote,
)
from .notifier import NotificationWorker, _send_message_with_retry
from .storage import Storage

LOGGER = logging.getLogger(__name__)
GROUP_TYPES = {"group", "supergroup"}
ACTIVE_MEMBER_STATUSES = {"member", "administrator", "creator"}
INACTIVE_MEMBER_STATUSES = {"left", "kicked"}

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


def create_router(
    *,
    storage: Storage,
    bot_id: int,
    bot_username: str,
    notification_worker: NotificationWorker | None = None,
    admin_user_ids: frozenset[int] = frozenset(),
) -> Router:
    router = Router(name=__name__)

    @router.message(CommandStart(), F.chat.type == "private")
    @router.message(Command("help"), F.chat.type == "private")
    async def private_start(message: Message) -> None:
        _remember(storage, message.from_user, private=True)
        await message.answer(
            "Готово! Теперь, когда вас упомянут в подключённом рабочем чате, "
            "я пришлю сюда уведомление и кнопку перехода к сообщению.",
            reply_markup=_private_keyboard(bot_username),
        )

    @router.message(Command("status"), F.chat.type == "private")
    async def private_status(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        if user_id not in admin_user_ids:
            await message.answer("Я на связи.")
            return
        stats = storage.queue_stats()
        await message.answer(
            "✅ Бот работает\n"
            f"В очереди: {stats.queued}\n"
            f"Доставлено за период хранения: {stats.sent}\n"
            f"Окончательно не доставлено: {stats.permanently_failed}"
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
                "Bot added chat_id=%s title=%r type=%s status=%s",
                event.chat.id,
                event.chat.title,
                _enum_value(event.chat.type),
                new_status,
            )
            await _send_message_with_retry(
                bot,
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
                "Cannot create message link chat_id=%s type=%s",
                message.chat.id,
                _enum_value(message.chat.type),
            )
            return

        target_ids: set[int] = set()
        unresolved_count = 0
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
                    unresolved_count += 1

        target_ids.discard(bot_id)
        allowed_target_ids = storage.filter_dm_allowed(target_ids)
        skipped_count = len(target_ids) - len(allowed_target_ids)
        if not allowed_target_ids:
            LOGGER.debug(
                "Mention skipped chat_id=%s message_id=%s unresolved=%s dm_disabled=%s",
                message.chat.id,
                message.message_id,
                unresolved_count,
                skipped_count,
            )
            return

        fallback = CONTENT_TITLES.get(
            _enum_value(message.content_type),
            "Новое сообщение",
        )
        body = notification_text(
            notification_context(short_quote(source_text, fallback=fallback))
        )
        inserted = storage.enqueue_notifications(
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_ids=allowed_target_ids,
            body_text=body,
            button_text=chat_button_label(message.chat.title),
            message_link=message_link,
        )
        if inserted and notification_worker is not None:
            notification_worker.wake()

        LOGGER.info(
            "Mention queued chat_id=%s message_id=%s recipients=%s "
            "duplicates=%s unresolved=%s dm_disabled=%s",
            message.chat.id,
            message.message_id,
            inserted,
            len(allowed_target_ids) - inserted,
            unresolved_count,
            skipped_count,
        )

    router.message.register(process_group_message)
    router.edited_message.register(process_group_message)
    return router
