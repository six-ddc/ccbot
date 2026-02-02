"""Message building and new message handling from session monitor.

Core responsibilities:
  - Build formatted message parts for Telegram delivery
  - Handle new messages from session monitor
  - Route messages to active users
  - Handle interactive tool prompts (AskUserQuestion, etc.)
  - Manage user read offsets

Key functions: build_response_parts(), handle_new_message()
"""

import asyncio
import logging
from pathlib import Path

from telegram import Bot

from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .markdown_v2 import convert_markdown
from .message_queue import enqueue_content_message
from .session import session_manager
from .session_monitor import NewMessage
from .telegram_sender import split_message

logger = logging.getLogger(__name__)

# Tool names that trigger interactive UI via JSONL (terminal capture + inline keyboard)
INTERACTIVE_TOOL_NAMES = frozenset({"AskUserQuestion", "ExitPlanMode"})


def build_response_parts(
    text: str, is_complete: bool,
    content_type: str = "text",
    role: str = "assistant",
) -> list[str]:
    """Build paginated response messages for Telegram.

    Returns a list of message strings, each within Telegram's 4096 char limit.
    Multi-part messages get a [1/N] suffix.
    """
    text = text.strip()

    # User messages: add emoji prefix (no newline)
    if role == "user":
        prefix = "👤 "
        separator = ""
        # User messages are typically short, no special processing needed
        if len(text) > 3000:
            text = text[:3000] + "…"
        return [convert_markdown(f"{prefix}{text}")]

    # Truncate thinking content to keep it compact
    if content_type == "thinking" and is_complete:
        from .transcript_parser import TranscriptParser
        start_tag = TranscriptParser.EXPANDABLE_QUOTE_START
        end_tag = TranscriptParser.EXPANDABLE_QUOTE_END
        max_thinking = 500
        if start_tag in text and end_tag in text:
            inner = text[text.index(start_tag) + len(start_tag):text.index(end_tag)]
            if len(inner) > max_thinking:
                inner = inner[:max_thinking] + "\n\n… (thinking truncated)"
            text = start_tag + inner + end_tag
        elif len(text) > max_thinking:
            text = text[:max_thinking] + "\n\n… (thinking truncated)"

    # Format based on content type
    if content_type == "thinking":
        # Thinking: prefix with "∴ Thinking…" and single newline
        prefix = "∴ Thinking…"
        separator = "\n"
    else:
        # Plain text: no prefix
        prefix = ""
        separator = ""

    # If text contains expandable quote sentinels, don't split —
    # the quote must stay atomic. Truncation is handled by
    # _render_expandable_quote in markdown_v2.py.
    from .transcript_parser import TranscriptParser
    if TranscriptParser.EXPANDABLE_QUOTE_START in text:
        if prefix:
            return [convert_markdown(f"{prefix}{separator}{text}")]
        else:
            return [convert_markdown(text)]

    # Split markdown first, then convert each chunk to HTML.
    # Use conservative max to leave room for HTML tags added by conversion.
    max_text = 3000 - len(prefix) - len(separator)

    text_chunks = split_message(text, max_length=max_text)
    total = len(text_chunks)

    if total == 1:
        if prefix:
            return [convert_markdown(f"{prefix}{separator}{text_chunks[0]}")]
        else:
            return [convert_markdown(text_chunks[0])]

    parts = []
    for i, chunk in enumerate(text_chunks, 1):
        if prefix:
            parts.append(convert_markdown(f"{prefix}{separator}{chunk}\n\n[{i}/{total}]"))
        else:
            parts.append(convert_markdown(f"{chunk}\n\n[{i}/{total}]"))
    return parts


async def handle_new_message(msg: NewMessage, bot: Bot) -> None:
    """Handle a new assistant message — enqueue for sequential processing.

    Messages are queued per-user to ensure status messages always appear last.
    """
    # Import here to avoid circular dependency
    from .message_queue import _message_queues
    
    status = "complete" if msg.is_complete else "streaming"
    logger.info(
        f"handle_new_message [{status}]: session={msg.session_id}, "
        f"text_len={len(msg.text)}"
    )

    # Find users whose active window matches this session
    active_users: list[tuple[int, str]] = []  # (user_id, window_name)
    for uid, wname in session_manager.active_sessions.items():
        resolved = await session_manager.resolve_session_for_window(wname)
        if resolved and resolved.session_id == msg.session_id:
            active_users.append((uid, wname))

    if not active_users:
        logger.info(
            f"No active users for session {msg.session_id}. "
            f"Active sessions: {dict(session_manager.active_sessions)}"
        )
        # Log what each active user resolves to, for debugging
        for uid, wname in session_manager.active_sessions.items():
            resolved = await session_manager.resolve_session_for_window(wname)
            resolved_id = resolved.session_id if resolved else None
            logger.info(
                f"  user={uid}, window={wname} -> resolved_session={resolved_id}"
            )
        return

    # Import interactive_msgs here to avoid circular dependency
    from .interactive_ui import _interactive_msgs, _interactive_mode

    for user_id, wname in active_users:
        # Handle interactive tools specially - capture terminal and send UI
        if msg.tool_name in INTERACTIVE_TOOL_NAMES and msg.content_type == "tool_use":
            # Mark interactive mode BEFORE sleeping so polling skips this window
            _interactive_mode[user_id] = wname
            # Flush pending messages (e.g. plan content) before sending interactive UI
            queue = _message_queues.get(user_id)
            if queue:
                await queue.join()
            # Wait briefly for Claude Code to render the question UI
            await asyncio.sleep(0.3)
            handled = await handle_interactive_ui(bot, user_id, wname)
            if handled:
                # Update user's read offset
                session = await session_manager.resolve_session_for_window(wname)
                if session and session.file_path:
                    try:
                        file_size = Path(session.file_path).stat().st_size
                        session_manager.update_user_window_offset(user_id, wname, file_size)
                    except OSError:
                        pass
                continue  # Don't send the normal tool_use message
            else:
                # UI not rendered — clear the early-set mode
                _interactive_mode.pop(user_id, None)

        # Any non-interactive message means the interaction is complete — delete the UI message
        if _interactive_msgs.get(user_id):
            await clear_interactive_msg(user_id, bot)

        parts = build_response_parts(
            msg.text, msg.is_complete, msg.content_type, msg.role,
        )

        if msg.is_complete:
            # Enqueue content message task
            # Note: tool_result editing is handled inside _process_content_task
            # to ensure sequential processing with tool_use message sending
            await enqueue_content_message(
                bot=bot,
                user_id=user_id,
                window_name=wname,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
            )

            # Update user's read offset to current file position
            # This marks these messages as "read" for this user
            session = await session_manager.resolve_session_for_window(wname)
            if session and session.file_path:
                try:
                    file_size = Path(session.file_path).stat().st_size
                    session_manager.update_user_window_offset(user_id, wname, file_size)
                except OSError:
                    pass
