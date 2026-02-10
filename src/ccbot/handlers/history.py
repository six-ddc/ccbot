"""Message history display with pagination.

Provides history viewing functionality for Claude Code sessions:
  - _build_history_keyboard: Build inline keyboard for page navigation
  - send_history: Send or edit message history with pagination support

Supports both full history and unread message range views.
"""

import logging
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ..config import config
from ..session import session_manager
from ..telegram_sender import split_message
from ..transcript_parser import TranscriptParser
from .callback_data import CB_HISTORY_NEXT, CB_HISTORY_PREV
from .message_sender import safe_edit, safe_reply, safe_send

logger = logging.getLogger(__name__)


def _build_history_keyboard(
    window_name: str,
    page_index: int,
    total_pages: int,
    start_byte: int = 0,
    end_byte: int = 0,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for history pagination.

    Callback format: hp:<page>:<window>:<start>:<end> or hn:<page>:<window>:<start>:<end>
    When start=0 and end=0, it means full history (no byte range filter).
    """
    if total_pages <= 1:
        return None

    buttons = []
    if page_index > 0:
        cb_data = (
            f"{CB_HISTORY_PREV}{page_index - 1}:{window_name}:{start_byte}:{end_byte}"
        )
        buttons.append(
            InlineKeyboardButton(
                "◀ Older",
                callback_data=cb_data[:64],
            )
        )

    buttons.append(
        InlineKeyboardButton(f"{page_index + 1}/{total_pages}", callback_data="noop")
    )

    if page_index < total_pages - 1:
        cb_data = (
            f"{CB_HISTORY_NEXT}{page_index + 1}:{window_name}:{start_byte}:{end_byte}"
        )
        buttons.append(
            InlineKeyboardButton(
                "Newer ▶",
                callback_data=cb_data[:64],
            )
        )

    return InlineKeyboardMarkup([buttons])


async def send_history(
    target: Any,
    window_name: str,
    offset: int = -1,
    edit: bool = False,
    *,
    start_byte: int = 0,
    end_byte: int = 0,
    user_id: int | None = None,
    bot: Bot | None = None,
    message_thread_id: int | None = None,
) -> None:
    """Send or edit message history for a window's session.

    Args:
        target: Message object (for reply) or CallbackQuery (for edit).
        window_name: Tmux window name (resolved to session via sent messages).
        offset: Page index (0-based). -1 means last page (for full history)
                or first page (for unread range).
        edit: If True, edit existing message instead of sending new one.
        start_byte: Start byte offset (0 = from beginning).
        end_byte: End byte offset (0 = to end of file).
        user_id: User ID for updating read offset (required for unread mode).
        bot: Bot instance for direct send mode (when edit=False and bot is provided).
        message_thread_id: Telegram topic thread_id for targeted send.
    """
    # Determine if this is unread mode (specific byte range)
    is_unread = start_byte > 0 or end_byte > 0
    logger.debug(
        "send_history: window=%s, offset=%d, is_unread=%s, byte_range=%d-%d",
        window_name,
        offset,
        is_unread,
        start_byte,
        end_byte,
    )

    messages, total = await session_manager.get_recent_messages(
        window_name,
        start_byte=start_byte,
        end_byte=end_byte if end_byte > 0 else None,
    )

    if total == 0:
        if is_unread:
            text = f"📬 [{window_name}] No unread messages."
        else:
            text = f"📋 [{window_name}] No messages yet."
        keyboard = None
    else:
        _start = TranscriptParser.EXPANDABLE_QUOTE_START
        _end = TranscriptParser.EXPANDABLE_QUOTE_END

        # Filter messages based on config
        if config.show_user_messages:
            # Keep both user and assistant messages
            pass
        else:
            # Filter to assistant messages only
            messages = [m for m in messages if m["role"] == "assistant"]
        total = len(messages)
        if total == 0:
            if is_unread:
                text = f"📬 [{window_name}] No unread messages."
            else:
                text = f"📋 [{window_name}] No messages yet."
            keyboard = None
            if edit:
                await safe_edit(target, text, reply_markup=keyboard)
            elif bot is not None and user_id is not None:
                await safe_send(
                    bot,
                    session_manager.resolve_chat_id(user_id, message_thread_id),
                    text,
                    message_thread_id=message_thread_id,
                    reply_markup=keyboard,
                )
            else:
                await safe_reply(target, text, reply_markup=keyboard)
            # Update offset even if no assistant messages
            if user_id is not None and end_byte > 0:
                session_manager.update_user_window_offset(
                    user_id, window_name, end_byte
                )
            return

        if is_unread:
            header = f"📬 [{window_name}] {total} unread messages"
        else:
            header = f"📋 [{window_name}] Messages ({total} total)"

        lines = [header]
        for msg in messages:
            # Format timestamp as HH:MM
            ts = msg.get("timestamp")
            if ts:
                try:
                    # ISO format: 2024-01-15T14:32:00.000Z
                    time_part = ts.split("T")[1] if "T" in ts else ts
                    hh_mm = time_part[:5]  # "14:32"
                except (IndexError, TypeError):
                    hh_mm = ""
            else:
                hh_mm = ""

            # Add separator with time
            if hh_mm:
                lines.append(f"───── {hh_mm} ─────")
            else:
                lines.append("─────────────")

            # Format message content
            msg_text = msg["text"]
            content_type = msg.get("content_type", "text")
            msg_role = msg.get("role", "assistant")

            # Strip expandable quote sentinels for history view
            msg_text = msg_text.replace(_start, "").replace(_end, "")

            # Add prefix based on role/type
            if msg_role == "user":
                # User message with emoji prefix (no newline)
                lines.append(f"👤 {msg_text}")
            elif content_type == "thinking":
                # Thinking prefix to match real-time format
                lines.append(f"∴ Thinking…\n{msg_text}")
            else:
                lines.append(msg_text)
        full_text = "\n\n".join(lines)
        pages = split_message(full_text, max_length=4096)

        # Default to last page (newest messages) for both history and unread
        if offset < 0:
            offset = len(pages) - 1
        page_index = max(0, min(offset, len(pages) - 1))
        text = pages[page_index]
        keyboard = _build_history_keyboard(
            window_name, page_index, len(pages), start_byte, end_byte
        )
        logger.debug(
            "send_history result: %d messages, %d pages, serving page %d",
            total,
            len(pages),
            page_index,
        )

    if edit:
        await safe_edit(target, text, reply_markup=keyboard)
    elif bot is not None and user_id is not None:
        # Direct send mode (for unread catch-up after window switch)
        await safe_send(
            bot,
            session_manager.resolve_chat_id(user_id, message_thread_id),
            text,
            message_thread_id=message_thread_id,
            reply_markup=keyboard,
        )
    else:
        await safe_reply(target, text, reply_markup=keyboard)

    # Update user's read offset after viewing unread
    if is_unread and user_id is not None and end_byte > 0:
        session_manager.update_user_window_offset(user_id, window_name, end_byte)
