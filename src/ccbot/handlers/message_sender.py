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

import logging
import time
from typing import Any

from telegram import Bot, LinkPreviewOptions, Message
from telegram.error import RetryAfter

from ..markdown_v2 import convert_markdown

logger = logging.getLogger(__name__)

# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Rate limiting: last send time per user to avoid Telegram flood control
_last_send_time: dict[int, float] = {}
MESSAGE_SEND_INTERVAL = 1.1  # seconds between messages to same user


async def rate_limit_send(user_id: int) -> None:
    """Wait if necessary to avoid Telegram flood control (max 1 msg/sec per user)."""
    import asyncio

    now = time.time()
    if user_id in _last_send_time:
        elapsed = now - _last_send_time[user_id]
        if elapsed < MESSAGE_SEND_INTERVAL:
            wait_time = MESSAGE_SEND_INTERVAL - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s for user {user_id}")
            await asyncio.sleep(wait_time)
    _last_send_time[user_id] = time.time()


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
    except Exception:
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter:
            raise
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
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


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message:
    """Reply with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await message.reply_text(
            convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except RetryAfter:
        raise
    except Exception:
        return await message.reply_text(text, **kwargs)


async def safe_edit(target: Any, text: str, **kwargs: Any) -> None:
    """Edit message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        await target.edit_message_text(
            convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except RetryAfter:
        raise
    except Exception:
        try:
            await target.edit_message_text(text, **kwargs)
        except RetryAfter:
            raise
        except Exception as e:
            logger.error("Failed to edit message: %s", e)


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
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter:
            raise
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
