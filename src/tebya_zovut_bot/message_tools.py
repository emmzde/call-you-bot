from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from aiogram.types import MessageEntity, User


class UnsupportedMessageLink(ValueError):
    """Raised when Telegram cannot link to an exact message."""


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
    """Build a Telegram link to a message in a group or supergroup."""
    if message_id <= 0:
        raise ValueError("message_id must be positive")

    if chat_username:
        base = f"https://t.me/{quote(chat_username.removeprefix('@'), safe='')}"
    else:
        serialized_id = str(chat_id)
        if serialized_id.startswith("-100") and len(serialized_id) > 4:
            base = f"https://t.me/c/{serialized_id[4:]}"
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
