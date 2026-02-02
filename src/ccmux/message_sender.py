"""Message sending utilities with MarkdownV2 conversion and rate limiting.

Core responsibilities:
  - Safe message sending with automatic MarkdownV2 conversion and fallback
  - Rate limiting to avoid Telegram flood control (1.1s between messages)
  - Message reply, edit, and send operations with error handling

Key functions: safe_reply(), safe_edit(), safe_send(), rate_limit_send()
"""

import asyncio
import logging
import time

from telegram import Bot, LinkPreviewOptions

from .markdown_v2 import convert_markdown

logger = logging.getLogger(__name__)

# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Rate limiting: last send time per user to avoid Telegram flood control
_last_send_time: dict[int, float] = {}
MESSAGE_SEND_INTERVAL = 1.1  # seconds between messages to same user


async def rate_limit_send(user_id: int) -> None:
    """Wait if necessary to avoid Telegram flood control (max 1 msg/sec per user)."""
    now = time.time()
    if user_id in _last_send_time:
        elapsed = now - _last_send_time[user_id]
        if elapsed < MESSAGE_SEND_INTERVAL:
            wait_time = MESSAGE_SEND_INTERVAL - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s for user {user_id}")
            await asyncio.sleep(wait_time)
    _last_send_time[user_id] = time.time()


async def safe_reply(message, text: str, **kwargs):  # type: ignore[no-untyped-def]
    """Reply with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await message.reply_text(
            convert_markdown(text), parse_mode="MarkdownV2", **kwargs,
        )
    except Exception:
        return await message.reply_text(text, **kwargs)


async def safe_edit(target, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Edit message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        await target.edit_message_text(
            convert_markdown(text), parse_mode="MarkdownV2", **kwargs,
        )
    except Exception:
        try:
            await target.edit_message_text(text, **kwargs)
        except Exception as e:
            logger.error("Failed to edit message: %s", e)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    """Send message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
            **kwargs,
        )
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
