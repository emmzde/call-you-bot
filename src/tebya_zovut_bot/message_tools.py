from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from aiogram.types import MessageEntity, User


class UnsupportedMessageLink(ValueError):
    """Raised when Telegram cannot link to an exact message."""


BUTTON_LABEL_MAX_WIDTH = 48
NOTIFICATION_TEXT = "Тебя зовут! Давай быстрее;)"


@dataclass(frozen=True, slots=True)
class Mention:
    username: str | None = None
    user: User | None = None


def extract_mentions(
    text: str | None, entities: list[MessageEntity] | None
) -> list[Mention]:
    if not text or not entities:
        return []

    mentions: list[Mention] = []
    seen: set[tuple[str, str | int]] = set()

    for entity in entities:
        mention: Mention | None = None
        if entity.type == "text_mention" and entity.user is not None:
            mention = Mention(user=entity.user)
            key: tuple[str, str | int] = ("id", entity.user.id)
        elif entity.type == "mention":
            extracted = entity.extract_from(text).strip()
            username = extracted.removeprefix("@")
            if not username:
                continue
            mention = Mention(username=username)
            key = ("username", username.casefold())
        else:
            continue

        if key not in seen:
            seen.add(key)
            mentions.append(mention)

    return mentions


def build_message_link(
    *,
    chat_id: int,
    chat_username: str | None,
    message_id: int,
    message_thread_id: int | None = None,
    single: bool = False,
) -> str:
    """Build an official Telegram deep link to a group message."""
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    if message_thread_id is not None and message_thread_id <= 0:
        raise ValueError("message_thread_id must be positive")

    if chat_username:
        username = chat_username.strip().removeprefix("@")
        if not username:
            raise UnsupportedMessageLink("The public chat username is empty")
        base = f"https://t.me/{quote(username, safe='')}"
    else:
        serialized_id = str(chat_id)
        if serialized_id.startswith("-100") and len(serialized_id) > 4:
            internal_id = serialized_id[4:]
            if not internal_id.isdigit() or int(internal_id) <= 0:
                raise UnsupportedMessageLink("The supergroup ID is invalid")
            base = f"https://t.me/c/{internal_id}"
        elif serialized_id.startswith("-") and len(serialized_id) > 1:
            # Telegram Android handles this native link for basic groups.
            # Basic group IDs from the Bot API are negative, whereas the
            # tg:// handler expects the positive peer ID.
            return (
                f"tg://openmessage?chat_id={serialized_id[1:]}&message_id={message_id}"
            )
        else:
            raise UnsupportedMessageLink(
                "A group message link requires a negative Telegram chat ID"
            )

    if message_thread_id and message_thread_id != message_id:
        path = f"/{message_thread_id}/{message_id}"
    else:
        path = f"/{message_id}"

    return f"{base}{path}{'?single' if single else ''}"


_WHITESPACE = re.compile(r"\s+")


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
    return width


def chat_button_label(
    chat_title: str | None,
    *,
    max_width: int = BUTTON_LABEL_MAX_WIDTH,
) -> str:
    """Return a compact, single-line chat title suitable for an inline button."""
    if max_width < 2:
        raise ValueError("max_width must be at least 2")

    title = _WHITESPACE.sub(" ", chat_title or "").strip() or "Открыть чат"
    if len(title) <= max_width and _display_width(title) <= max_width:
        return title

    result: list[str] = []
    current_width = 0
    target_width = max_width - 1
    for character in title:
        if len(result) >= target_width:
            break
        character_width = _display_width(character)
        if current_width + character_width > target_width:
            break
        result.append(character)
        current_width += character_width

    compact = "".join(result).rstrip(" -–—_\u200d\ufe0f")
    return (compact or title[:1]) + "…"


def short_quote(
    text: str | None,
    *,
    fallback: str = "Новое сообщение",
    max_length: int = 140,
) -> str:
    normalized = _WHITESPACE.sub(" ", text or "").strip()
    if not normalized:
        return fallback
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"


def notification_context(quote_text: str) -> str:
    return f"Текст сообщения: {quote_text}"


def notification_text(context: str) -> str:
    return f"{NOTIFICATION_TEXT}\n\n{context}"
