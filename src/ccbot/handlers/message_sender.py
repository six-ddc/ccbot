"""Safe message sending helpers with MarkdownV2 fallback.

Provides utility functions for sending Telegram messages with automatic
conversion to MarkdownV2 format and fallback to plain text on failure.

Functions:
  - rate_limit_send: Rate limiter to avoid Telegram flood control
  - rate_limit_send_message: Combined rate limiting + send with fallback
  - safe_reply: Reply with MarkdownV2, fallback to plain text
  - safe_edit: Edit message with MarkdownV2, fallback to plain text
  - safe_send: Send message with MarkdownV2, fallback to plain text
"""

import asyncio
import logging
import time
from typing import Any

from telegram import Bot, LinkPreviewOptions, Message
from telegram.error import BadRequest, RetryAfter, TelegramError

from ..markdown_v2 import convert_markdown

logger = logging.getLogger(__name__)

# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Rate limiting: last send time per chat to avoid Telegram flood control
_last_send_time: dict[int, float] = {}
MESSAGE_SEND_INTERVAL = 1.1  # seconds between messages to same chat


async def rate_limit_send(chat_id: int) -> None:
    """Wait if necessary to avoid Telegram flood control (max 1 msg/sec per chat)."""
    now = time.monotonic()
    if chat_id in _last_send_time:
        elapsed = now - _last_send_time[chat_id]
        if elapsed < MESSAGE_SEND_INTERVAL:
            await asyncio.sleep(MESSAGE_SEND_INTERVAL - elapsed)
    _last_send_time[chat_id] = time.monotonic()


async def _send_with_fallback(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    """Send message with MarkdownV2, falling back to plain text on failure.

    Internal helper that handles the MarkdownV2 → plain text fallback pattern.
    Returns the sent Message on success, None on failure.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter:
            raise
        except TelegramError as e:
            logger.warning("Failed to send message to %s: %s", chat_id, e)
            return None


async def rate_limit_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    """Rate-limited send with MarkdownV2 fallback.

    Combines rate_limit_send() + _send_with_fallback() for convenience.
    The chat_id should be the group chat ID for forum topics, or the user ID
    for direct messages.  Use session_manager.resolve_chat_id() to obtain it.
    Returns the sent Message on success, None on failure.
    """
    await rate_limit_send(chat_id)
    return await _send_with_fallback(bot, chat_id, text, **kwargs)


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message | None:
    """Reply with MarkdownV2, falling back to plain text on failure.

    Returns None if the original message no longer exists (e.g. deleted topic).
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await message.reply_text(
            convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except BadRequest as exc:
        if "not found" in str(exc).lower():
            logger.warning("Cannot reply: original message gone (%s)", exc)
            return None
        raise
    except RetryAfter:
        raise
    except TelegramError:
        return await message.reply_text(text, **kwargs)


async def safe_edit(target: Any, text: str, **kwargs: Any) -> None:
    """Edit message with MarkdownV2, falling back to plain text on failure.

    Accepts either a CallbackQuery (edit_message_text) or a Message (edit_text).
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    # Message.edit_text vs CallbackQuery.edit_message_text
    edit_fn = (
        target.edit_text if isinstance(target, Message) else target.edit_message_text
    )
    try:
        await edit_fn(
            convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            await edit_fn(text, **kwargs)
        except RetryAfter:
            raise
        except TelegramError as e:
            logger.warning("Failed to edit message: %s", e)


async def safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Send message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    if message_thread_id is not None:
        kwargs.setdefault("message_thread_id", message_thread_id)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter:
            raise
        except TelegramError as e:
            logger.warning("Failed to send message to %s: %s", chat_id, e)
