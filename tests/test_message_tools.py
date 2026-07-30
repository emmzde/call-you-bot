import pytest
from aiogram.types import MessageEntity, User

from tebya_zovut_bot.handlers import GROUP_WELCOME_TEXT
from tebya_zovut_bot.message_tools import (
    BUTTON_LABEL_MAX_WIDTH,
    UnsupportedMessageLink,
    build_message_link,
    chat_button_label,
    extract_mentions,
    notification_context,
    notification_text,
    short_quote,
)


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_extracts_username_mention_after_emoji() -> None:
    prefix = "🚀 Зовём "
    username = "@ReleaseLead"
    text = prefix + username
    entity = MessageEntity(
        type="mention",
        offset=utf16_length(prefix),
        length=utf16_length(username),
    )

    assert extract_mentions(text, [entity])[0].username == "ReleaseLead"


def test_extracts_text_mention_user() -> None:
    user = User(id=42, is_bot=False, first_name="Аня")
    entity = MessageEntity(
        type="text_mention",
        offset=0,
        length=utf16_length("Аня"),
        user=user,
    )

    assert extract_mentions("Аня", [entity])[0].user == user


def test_deduplicates_the_same_username_case_insensitively() -> None:
    text = "@Ivan @IVAN"
    entities = [
        MessageEntity(type="mention", offset=0, length=5),
        MessageEntity(type="mention", offset=6, length=5),
    ]

    assert len(extract_mentions(text, entities)) == 1


def test_builds_public_supergroup_link() -> None:
    assert (
        build_message_link(
            chat_id=-100123456,
            chat_username="release_chat",
            message_id=77,
        )
        == "https://t.me/release_chat/77"
    )


def test_builds_private_forum_message_link() -> None:
    assert (
        build_message_link(
            chat_id=-100987654321,
            chat_username=None,
            message_thread_id=55,
            message_id=77,
            single=True,
        )
        == "https://t.me/c/987654321/55/77?single"
    )


def test_builds_public_forum_message_link() -> None:
    assert (
        build_message_link(
            chat_id=-100987654321,
            chat_username="@release_chat",
            message_thread_id=55,
            message_id=77,
        )
        == "https://t.me/release_chat/55/77"
    )


def test_general_topic_does_not_duplicate_message_id() -> None:
    assert (
        build_message_link(
            chat_id=-100987654321,
            chat_username=None,
            message_thread_id=77,
            message_id=77,
        )
        == "https://t.me/c/987654321/77"
    )


def test_builds_android_basic_group_link() -> None:
    assert (
        build_message_link(
            chat_id=-123456,
            chat_username=None,
            message_id=77,
        )
        == "tg://openmessage?chat_id=123456&message_id=77"
    )


def test_rejects_non_group_chat_id() -> None:
    with pytest.raises(UnsupportedMessageLink):
        build_message_link(chat_id=123456, chat_username=None, message_id=77)


def test_short_quote_and_context() -> None:
    source = "  Релиз\nготов.  " + "x" * 150
    quote = short_quote(source, max_length=30)

    assert len(quote) == 30
    assert quote.endswith("…")
    assert notification_context(quote) == f"Текст сообщения: {quote}"
    assert notification_text("Текст сообщения: test").endswith("Текст сообщения: test")


def test_chat_button_label_is_compact_and_single_line() -> None:
    label = chat_button_label("  Очень длинное\nназвание рабочего чата " + "🚀" * 40)

    assert "\n" not in label
    assert label.endswith("…")
    assert len(label) <= BUTTON_LABEL_MAX_WIDTH


def test_chat_button_label_has_safe_fallback() -> None:
    assert chat_button_label(None) == "Открыть чат"


def test_welcome_text_matches_product_copy() -> None:
    assert GROUP_WELCOME_TEXT == (
        "Бот «Тебя зовут!» подключён.\n\n"
        "Каждому участнику нужно один раз открыть бота по кнопке ниже и нажать "
        "Start — Telegram запрещает ботам первыми начинать личный диалог.\n"
        "Это тестовый экземпляр, не судите строго. By hiraeth"
    )
