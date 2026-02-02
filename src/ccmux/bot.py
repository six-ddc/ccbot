"""Telegram bot handlers — the main UI layer of CCMux.

Registers all command/callback/message handlers and manages the bot lifecycle.
Core responsibilities:
  - Command handlers: /start, /list, /history, /screenshot, /esc, plus
    forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: session selection, directory browser, history
    pagination, interactive UI navigation, screenshot refresh.
  - Per-user message queue + worker: ensures ordered delivery, merges
    consecutive content messages, and converts status→content in-place.
  - Status polling loop: polls terminal status lines for all active users.
  - Interactive UI: detects AskUserQuestion/ExitPlanMode/PermissionPrompt
    in terminal output and renders inline-keyboard navigation.

Key functions: create_bot(), handle_new_message(), send_history().
"""

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    LinkPreviewOptions,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import config
from .handlers import (
    CC_COMMANDS,
    callback_handler,
    esc_command,
    forward_command_handler,
    history_command,
    list_command,
    screenshot_command,
    start_command,
    text_handler,
)
from .markdown_v2 import convert_markdown
from .screenshot import text_to_image
from .session import session_manager
from .session_monitor import NewMessage, SessionMonitor
from .telegram_sender import split_message
from .terminal_parser import extract_interactive_content, is_interactive_ui, parse_status_line
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Disable link previews in all messages to reduce visual noise
_NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Session monitor instance
session_monitor: SessionMonitor | None = None

# Status polling task
_status_poll_task: asyncio.Task | None = None
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)

# Rate limiting: last send time per user to avoid Telegram flood control
_last_send_time: dict[int, float] = {}
MESSAGE_SEND_INTERVAL = 1.1  # seconds between messages to same user

# Map (tool_use_id, user_id) -> telegram message_id for editing tool_use messages with results
_tool_msg_ids: dict[tuple[str, int], int] = {}

# Status message tracking: user_id -> (message_id, window_name, last_text)
# Note: last_text may be missing in old entries during rolling update
_status_msg_info: dict[int, tuple[int, str] | tuple[int, str, str]] = {}

async def _rate_limit_send(user_id: int) -> None:
    """Wait if necessary to avoid Telegram flood control (max 1 msg/sec per user)."""
    now = time.time()
    if user_id in _last_send_time:
        elapsed = now - _last_send_time[user_id]
        if elapsed < MESSAGE_SEND_INTERVAL:
            wait_time = MESSAGE_SEND_INTERVAL - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s for user {user_id}")
            await asyncio.sleep(wait_time)
    _last_send_time[user_id] = time.time()


# --- Message queue management ---


@dataclass
class MessageTask:
    """Message task for queue processing."""

    task_type: Literal["content", "status_update", "status_clear"]
    text: str | None = None
    window_name: str | None = None
    # content type fields
    parts: list[str] = field(default_factory=list)
    tool_use_id: str | None = None
    content_type: str = "text"


# Per-user message queues and worker tasks
_message_queues: dict[int, asyncio.Queue[MessageTask]] = {}
_queue_workers: dict[int, asyncio.Task[None]] = {}
_queue_locks: dict[int, asyncio.Lock] = {}  # Protect drain/refill operations


def _get_or_create_queue(bot: Bot, user_id: int) -> asyncio.Queue[MessageTask]:
    """Get or create message queue and worker for a user."""
    if user_id not in _message_queues:
        _message_queues[user_id] = asyncio.Queue()
        _queue_locks[user_id] = asyncio.Lock()
        # Start worker task for this user
        _queue_workers[user_id] = asyncio.create_task(
            _message_queue_worker(bot, user_id)
        )
    return _message_queues[user_id]


def _inspect_queue(queue: asyncio.Queue[MessageTask]) -> list[MessageTask]:
    """Non-destructively inspect all items in queue.

    Drains the queue and returns all items. Caller must refill.
    """
    items: list[MessageTask] = []
    while not queue.empty():
        try:
            item = queue.get_nowait()
            items.append(item)
        except asyncio.QueueEmpty:
            break
    return items


def _can_merge_tasks(base: MessageTask, candidate: MessageTask) -> bool:
    """Check if two content tasks can be merged."""
    if base.window_name != candidate.window_name:
        return False
    if candidate.task_type != "content":
        return False
    # tool_use/tool_result break merge chain
    # - tool_use: will be edited later by tool_result
    # - tool_result: edits previous message, merging would cause order issues
    if base.content_type in ("tool_use", "tool_result"):
        return False
    if candidate.content_type in ("tool_use", "tool_result"):
        return False
    return True


MERGE_MAX_LENGTH = 3800  # Leave room for markdown conversion overhead


async def _merge_content_tasks(
    queue: asyncio.Queue[MessageTask],
    first: MessageTask,
    lock: asyncio.Lock,
) -> tuple[MessageTask, int]:
    """Merge consecutive content tasks from queue.

    Returns: (merged_task, merge_count) where merge_count is the number of
    additional tasks merged (0 if no merging occurred).
    """
    merged_parts = list(first.parts)
    current_length = sum(len(p) for p in merged_parts)
    merge_count = 0

    async with lock:
        items = _inspect_queue(queue)
        remaining: list[MessageTask] = []

        for i, task in enumerate(items):
            if not _can_merge_tasks(first, task):
                # Can't merge, keep this and all remaining items
                remaining = items[i:]
                break

            # Check length before merging
            task_length = sum(len(p) for p in task.parts)
            if current_length + task_length > MERGE_MAX_LENGTH:
                # Too long, stop merging
                remaining = items[i:]
                break

            merged_parts.extend(task.parts)
            current_length += task_length
            merge_count += 1

        # Put remaining items back into the queue
        for item in remaining:
            queue.put_nowait(item)
            # Compensate: this item was already counted when first enqueued,
            # put_nowait adds a duplicate count that must be removed
            queue.task_done()

    if merge_count == 0:
        return first, 0

    return MessageTask(
        task_type="content",
        window_name=first.window_name,
        parts=merged_parts,
        tool_use_id=first.tool_use_id,
        content_type=first.content_type,
    ), merge_count


async def _message_queue_worker(bot: Bot, user_id: int) -> None:
    """Process message tasks for a user sequentially."""
    queue = _message_queues[user_id]
    lock = _queue_locks[user_id]
    logger.info(f"Message queue worker started for user {user_id}")

    while True:
        try:
            task = await queue.get()
            try:
                if task.task_type == "content":
                    # Try to merge consecutive content tasks
                    merged_task, merge_count = await _merge_content_tasks(
                        queue, task, lock
                    )
                    if merge_count > 0:
                        logger.debug(
                            f"Merged {merge_count} tasks for user {user_id}"
                        )
                        # Mark merged tasks as done
                        for _ in range(merge_count):
                            queue.task_done()
                    await _process_content_task(bot, user_id, merged_task)
                elif task.task_type == "status_update":
                    await _process_status_update_task(bot, user_id, task)
                elif task.task_type == "status_clear":
                    await _do_clear_status_message(bot, user_id)
            except Exception as e:
                logger.error(f"Error processing message task for user {user_id}: {e}")
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"Message queue worker cancelled for user {user_id}")
            break
        except Exception as e:
            logger.error(f"Unexpected error in queue worker for user {user_id}: {e}")


async def _process_content_task(bot: Bot, user_id: int, task: MessageTask) -> None:
    """Process a content message task."""
    wname = task.window_name or ""

    # 1. Handle tool_result editing (merged parts are edited together)
    if task.content_type == "tool_result" and task.tool_use_id:
        _tkey = (task.tool_use_id, user_id)
        edit_msg_id = _tool_msg_ids.pop(_tkey, None)
        if edit_msg_id is not None:
            # Clear status message first
            await _do_clear_status_message(bot, user_id)
            # Join all parts for editing (merged content goes together)
            full_text = "\n\n".join(task.parts)
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=edit_msg_id,
                    text=full_text,
                    parse_mode="MarkdownV2",
                    link_preview_options=_NO_LINK_PREVIEW,
                )
                await _check_and_send_status(bot, user_id, wname)
                return
            except Exception:
                try:
                    # Fallback: strip markdown
                    plain_text = task.text or full_text
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=edit_msg_id,
                        text=plain_text,
                        link_preview_options=_NO_LINK_PREVIEW,
                    )
                    await _check_and_send_status(bot, user_id, wname)
                    return
                except Exception:
                    logger.debug(f"Failed to edit tool msg {edit_msg_id}, sending new")
                    # Fall through to send as new message

    # 2. Send content messages, converting status message to first content part
    first_part = True
    last_msg_id: int | None = None
    for part in task.parts:
        sent = None

        # For first part, try to convert status message to content (edit instead of delete)
        if first_part:
            first_part = False
            converted_msg_id = await _convert_status_to_content(bot, user_id, wname, part)
            if converted_msg_id is not None:
                last_msg_id = converted_msg_id
                continue

        await _rate_limit_send(user_id)
        try:
            sent = await bot.send_message(
                chat_id=user_id, text=part, parse_mode="MarkdownV2",
                link_preview_options=_NO_LINK_PREVIEW,
            )
        except Exception:
            try:
                sent = await bot.send_message(
                    chat_id=user_id, text=part,
                    link_preview_options=_NO_LINK_PREVIEW,
                )
            except Exception as e:
                logger.error(f"Failed to send message to {user_id}: {e}")

        if sent:
            last_msg_id = sent.message_id

    # 3. Record tool_use message ID for later editing
    if last_msg_id and task.tool_use_id and task.content_type == "tool_use":
        _tool_msg_ids[(task.tool_use_id, user_id)] = last_msg_id

    # 4. After content, check and send status
    await _check_and_send_status(bot, user_id, wname)


async def _convert_status_to_content(
    bot: Bot, user_id: int, window_name: str, content_text: str
) -> int | None:
    """Convert status message to content message by editing it.

    Returns the message_id if converted successfully, None otherwise.
    """
    info = _status_msg_info.pop(user_id, None)
    if not info:
        return None

    # Handle both old (2-tuple) and new (3-tuple) format
    msg_id = info[0]
    stored_wname = info[1]
    if stored_wname != window_name:
        # Different window, just delete the old status
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
        return None

    # Edit status message to show content
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=msg_id,
            text=content_text,
            parse_mode="MarkdownV2",
            link_preview_options=_NO_LINK_PREVIEW,
        )
        return msg_id
    except Exception:
        try:
            # Fallback to plain text
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=msg_id,
                text=content_text,
                link_preview_options=_NO_LINK_PREVIEW,
            )
            return msg_id
        except Exception as e:
            logger.debug(f"Failed to convert status to content: {e}")
            # Message might be deleted or too old, caller will send new message
            return None


async def _process_status_update_task(bot: Bot, user_id: int, task: MessageTask) -> None:
    """Process a status update task."""
    wname = task.window_name or ""
    status_text = task.text or ""

    if not status_text:
        # No status text means clear status
        await _do_clear_status_message(bot, user_id)
        return

    # Send typing indicator if Claude is interruptible (working)
    if "esc to interrupt" in status_text.lower():
        try:
            await bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
        except Exception:
            pass

    current_info = _status_msg_info.get(user_id)

    if current_info:
        # Handle both old (2-tuple) and new (3-tuple) format for compatibility
        if len(current_info) == 2:
            msg_id, stored_wname = current_info
            last_text = ""
        else:
            msg_id, stored_wname, last_text = current_info

        if stored_wname != wname:
            # Window changed - delete old and send new
            await _do_clear_status_message(bot, user_id)
            await _do_send_status_message(bot, user_id, wname, status_text)
        elif status_text == last_text:
            # Same content, skip edit
            pass
        else:
            # Same window, text changed - edit in place
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=convert_markdown(status_text),
                    parse_mode="MarkdownV2",
                    link_preview_options=_NO_LINK_PREVIEW,
                )
                _status_msg_info[user_id] = (msg_id, wname, status_text)
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=status_text,
                        link_preview_options=_NO_LINK_PREVIEW,
                    )
                    _status_msg_info[user_id] = (msg_id, wname, status_text)
                except Exception as e:
                    logger.debug(f"Failed to edit status message: {e}")
                    _status_msg_info.pop(user_id, None)
                    await _do_send_status_message(bot, user_id, wname, status_text)
    else:
        # No existing status message, send new
        await _do_send_status_message(bot, user_id, wname, status_text)


async def _do_send_status_message(
    bot: Bot, user_id: int, window_name: str, text: str
) -> None:
    """Send a new status message and track it (internal, called from worker)."""
    await _rate_limit_send(user_id)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
            link_preview_options=_NO_LINK_PREVIEW,
        )
        _status_msg_info[user_id] = (sent.message_id, window_name, text)
    except Exception:
        try:
            sent = await bot.send_message(
                chat_id=user_id, text=text,
                link_preview_options=_NO_LINK_PREVIEW,
            )
            _status_msg_info[user_id] = (sent.message_id, window_name, text)
        except Exception as e:
            logger.error(f"Failed to send status message to {user_id}: {e}")


async def _do_clear_status_message(bot: Bot, user_id: int) -> None:
    """Delete the status message for a user (internal, called from worker)."""
    info = _status_msg_info.pop(user_id, None)
    if info:
        msg_id = info[0]
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception as e:
            logger.debug(f"Failed to delete status message {msg_id}: {e}")


async def _check_and_send_status(bot: Bot, user_id: int, window_name: str) -> None:
    """Check terminal for status line and send status message if present."""
    # Skip if there are more messages pending in the queue
    queue = _message_queues.get(user_id)
    if queue and not queue.empty():
        return
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return

    status_line = parse_status_line(pane_text)
    if status_line:
        await _do_send_status_message(bot, user_id, window_name, status_line)


# Interactive UI callback prefixes (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"       # aq:up:<window>
CB_ASK_DOWN = "aq:down:"   # aq:down:<window>
CB_ASK_LEFT = "aq:left:"   # aq:left:<window>
CB_ASK_RIGHT = "aq:right:" # aq:right:<window>
CB_ASK_ESC = "aq:esc:"     # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:" # aq:enter:<window>
CB_ASK_REFRESH = "aq:ref:" # aq:ref:<window>

# Callback data prefixes used by internal functions
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"
CB_SESSION_HISTORY = "sa:hist:"
CB_SESSION_REFRESH = "sa:ref:"
CB_SESSION_KILL = "sa:kill:"
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Directories per page in directory browser
DIRS_PER_PAGE = 6

# User state keys (used by callback_handler in bot.py)
STATE_KEY = "state"
STATE_BROWSING_DIRECTORY = "browsing_directory"
BROWSE_PATH_KEY = "browse_path"
BROWSE_PAGE_KEY = "browse_page"
BROWSE_DIRS_KEY = "browse_dirs"

# List inline callback prefixes
CB_LIST_SELECT = "ls:sel:"
CB_LIST_NEW = "ls:new"

# Track interactive UI message IDs: user_id -> message_id
_interactive_msgs: dict[int, int] = {}

# Tool names that trigger interactive UI via JSONL (terminal capture + inline keyboard)
INTERACTIVE_TOOL_NAMES = frozenset({"AskUserQuestion", "ExitPlanMode"})

# Track interactive mode: user_id -> window_name (None if not in interactive mode)
_interactive_mode: dict[int, str] = {}


def is_user_allowed(user_id: int | None) -> bool:
    """Check if user is authorized to use the bot."""
    return user_id is not None and config.is_user_allowed(user_id)


def _clear_browse_state(user_data: dict | None) -> None:
    """Clear directory browsing state keys from user_data."""
    if user_data is not None:
        user_data.pop(STATE_KEY, None)
        user_data.pop("browse_path", None)
        user_data.pop("browse_page", None)
        user_data.pop("browse_dirs", None)


async def _build_session_detail(
    window_name: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build session detail text and action buttons for a window."""
    session = await session_manager.resolve_session_for_window(window_name)
    if session:
        detail_text = (
            f"📤 *Selected: {window_name}*\n\n"
            f"📝 {session.summary}\n"
            f"💬 {session.message_count} messages\n\n"
            f"Send text to forward to Claude."
        )
    else:
        detail_text = f"📤 *Selected: {window_name}*\n\nSend text to forward to Claude."
    # Encode callback data with byte-safe truncation
    action_buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 History", callback_data=f"{CB_SESSION_HISTORY}{window_name}"[:64]),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"{CB_SESSION_REFRESH}{window_name}"[:64]),
        InlineKeyboardButton("❌ Kill", callback_data=f"{CB_SESSION_KILL}{window_name}"[:64]),
    ]])
    return detail_text, action_buttons


async def _safe_reply(message, text: str, **kwargs):  # type: ignore[no-untyped-def]
    """Reply with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", _NO_LINK_PREVIEW)
    try:
        return await message.reply_text(
            convert_markdown(text), parse_mode="MarkdownV2", **kwargs,
        )
    except Exception:
        return await message.reply_text(text, **kwargs)


async def _safe_edit(target, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Edit message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", _NO_LINK_PREVIEW)
    try:
        await target.edit_message_text(
            convert_markdown(text), parse_mode="MarkdownV2", **kwargs,
        )
    except Exception:
        try:
            await target.edit_message_text(text, **kwargs)
        except Exception as e:
            logger.error("Failed to edit message: %s", e)


async def _safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    """Send message with MarkdownV2, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", _NO_LINK_PREVIEW)
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


# --- Message history ---

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
        cb_data = f"{CB_HISTORY_PREV}{page_index - 1}:{window_name}:{start_byte}:{end_byte}"
        buttons.append(InlineKeyboardButton(
            "◀ Older",
            callback_data=cb_data[:64],
        ))

    buttons.append(InlineKeyboardButton(f"{page_index + 1}/{total_pages}", callback_data="noop"))

    if page_index < total_pages - 1:
        cb_data = f"{CB_HISTORY_NEXT}{page_index + 1}:{window_name}:{start_byte}:{end_byte}"
        buttons.append(InlineKeyboardButton(
            "Newer ▶",
            callback_data=cb_data[:64],
        ))

    return InlineKeyboardMarkup([buttons])


async def send_history(
    target,
    window_name: str,
    offset: int = -1,
    edit: bool = False,
    *,
    start_byte: int = 0,
    end_byte: int = 0,
    user_id: int | None = None,
    bot: Bot | None = None,
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
    """
    # Determine if this is unread mode (specific byte range)
    is_unread = start_byte > 0 or end_byte > 0

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
        from .transcript_parser import TranscriptParser
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
                await _safe_edit(target, text, reply_markup=keyboard)
            elif bot is not None and user_id is not None:
                await _safe_send(bot, user_id, text, reply_markup=keyboard)
            else:
                await _safe_reply(target, text, reply_markup=keyboard)
            # Update offset even if no assistant messages
            if user_id is not None and end_byte > 0:
                session_manager.update_user_window_offset(user_id, window_name, end_byte)
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

    if edit:
        await _safe_edit(target, text, reply_markup=keyboard)
    elif bot is not None and user_id is not None:
        # Direct send mode (for unread catch-up after window switch)
        await _safe_send(bot, user_id, text, reply_markup=keyboard)
    else:
        await _safe_reply(target, text, reply_markup=keyboard)

    # Update user's read offset after viewing unread
    if is_unread and user_id is not None and end_byte > 0:
        session_manager.update_user_window_offset(user_id, window_name, end_byte)


# --- Directory browser ---

def build_directory_browser(current_path: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    """Build directory browser UI.

    Returns: (text, keyboard, subdirs) where subdirs is the full list for caching.
    """
    path = Path(current_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        path = Path.cwd()

    try:
        subdirs = sorted([
            d.name for d in path.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        ])
    except (PermissionError, OSError):
        subdirs = []

    total_pages = max(1, (len(subdirs) + DIRS_PER_PAGE - 1) // DIRS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DIRS_PER_PAGE
    page_dirs = subdirs[start:start + DIRS_PER_PAGE]

    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(page_dirs), 2):
        row = []
        for j, name in enumerate(page_dirs[i:i+2]):
            display = name[:12] + "…" if len(name) > 13 else name
            # Use global index (start + i + j) to avoid long dir names in callback_data
            idx = start + i + j
            row.append(InlineKeyboardButton(f"📁 {display}", callback_data=f"{CB_DIR_SELECT}{idx}"))
        buttons.append(row)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀", callback_data=f"{CB_DIR_PAGE}{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶", callback_data=f"{CB_DIR_PAGE}{page+1}"))
        buttons.append(nav)

    action_row: list[InlineKeyboardButton] = []
    # Allow going up unless at filesystem root
    if path != path.parent:
        action_row.append(InlineKeyboardButton("..", callback_data=CB_DIR_UP))
    action_row.append(InlineKeyboardButton("Select", callback_data=CB_DIR_CONFIRM))
    action_row.append(InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL))
    buttons.append(action_row)

    display_path = str(path).replace(str(Path.home()), "~")
    if not subdirs:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\n_(No subdirectories)_"
    else:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\nTap a folder to enter, or select current directory"

    return text, InlineKeyboardMarkup(buttons), subdirs


# --- Status line polling ---



async def _enqueue_status_update(bot: Bot, user_id: int, window_name: str, status_text: str | None) -> None:
    """Enqueue status update."""
    queue = _get_or_create_queue(bot, user_id)

    if status_text:
        task = MessageTask(
            task_type="status_update",
            text=status_text,
            window_name=window_name,
        )
    else:
        task = MessageTask(task_type="status_clear")

    queue.put_nowait(task)


async def _update_status_message(bot: Bot, user_id: int, window_name: str) -> None:
    """Poll terminal and enqueue status update for user's active window.

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        # Window gone, enqueue clear
        await _enqueue_status_update(bot, user_id, window_name, None)
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    interactive_window = _get_interactive_window(user_id)
    should_check_new_ui = True

    if interactive_window == window_name:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await _clear_interactive_msg(user_id, bot)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await _clear_interactive_msg(user_id, bot)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    if should_check_new_ui and is_interactive_ui(pane_text):
        await _handle_interactive_ui(bot, user_id, window_name)
        return

    # Normal status line check
    status_line = parse_status_line(pane_text)

    if status_line:
        await _enqueue_status_update(bot, user_id, window_name, status_line)
    # If no status line, keep existing status message (don't clear on transient state)


async def _enqueue_content_message(
    bot: Bot,
    user_id: int,
    window_name: str,
    parts: list[str],
    tool_use_id: str | None = None,
    content_type: str = "text",
    text: str | None = None,
) -> None:
    """Enqueue a content message task."""
    queue = _get_or_create_queue(bot, user_id)

    task = MessageTask(
        task_type="content",
        text=text,
        window_name=window_name,
        parts=parts,
        tool_use_id=tool_use_id,
        content_type=content_type,
    )
    queue.put_nowait(task)


# --- Interactive UI handling (AskUserQuestion / ExitPlanMode / Permission Prompt) ---


def _build_interactive_keyboard(
    window_name: str, ui_name: str = "",
) -> InlineKeyboardMarkup:
    """Build keyboard for interactive UI navigation.

    ``ui_name`` controls the layout: ``RestoreCheckpoint`` omits ←/→ keys
    since only vertical selection is needed.
    """
    vertical_only = ui_name == "RestoreCheckpoint"

    rows: list[list[InlineKeyboardButton]] = []
    # Row 1: directional keys
    rows.append([
        InlineKeyboardButton("↑", callback_data=f"{CB_ASK_UP}{window_name}"[:64]),
    ])
    if vertical_only:
        rows.append([
            InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{window_name}"[:64]),
        ])
    else:
        rows.append([
            InlineKeyboardButton("←", callback_data=f"{CB_ASK_LEFT}{window_name}"[:64]),
            InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{window_name}"[:64]),
            InlineKeyboardButton("→", callback_data=f"{CB_ASK_RIGHT}{window_name}"[:64]),
        ])
    # Row 2: action keys
    rows.append([
        InlineKeyboardButton("⎋ Esc", callback_data=f"{CB_ASK_ESC}{window_name}"[:64]),
        InlineKeyboardButton("🔄", callback_data=f"{CB_ASK_REFRESH}{window_name}"[:64]),
        InlineKeyboardButton("⏎ Enter", callback_data=f"{CB_ASK_ENTER}{window_name}"[:64]),
    ])
    return InlineKeyboardMarkup(rows)


async def _handle_interactive_ui(
    bot: Bot,
    user_id: int,
    window_name: str,
) -> bool:
    """Capture terminal and send interactive UI content to user.

    Handles AskUserQuestion, ExitPlanMode, Permission Prompt, and
    RestoreCheckpoint UIs. Returns True if UI was detected and sent,
    False otherwise.
    """
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        return False

    # Capture plain text (no ANSI colors)
    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return False

    # Quick check if it looks like an interactive UI
    if not is_interactive_ui(pane_text):
        return False

    # Extract content between separators
    content = extract_interactive_content(pane_text)
    if not content:
        return False

    # Build message with navigation keyboard
    keyboard = _build_interactive_keyboard(window_name, ui_name=content.name)

    # Send as plain text (no markdown conversion)
    text = content.content

    # Check if we have an existing interactive message to edit
    existing_msg_id = _interactive_msgs.get(user_id)
    if existing_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=keyboard,
                link_preview_options=_NO_LINK_PREVIEW,
            )
            _interactive_mode[user_id] = window_name
            return True
        except Exception:
            # Message unchanged or other error - silently ignore, don't send new
            return True

    # Send new message
    await _rate_limit_send(user_id)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=keyboard,
            link_preview_options=_NO_LINK_PREVIEW,
        )
        _interactive_msgs[user_id] = sent.message_id
        _interactive_mode[user_id] = window_name
    except Exception as e:
        logger.error(f"Failed to send interactive UI to {user_id}: {e}")
        return False

    return True


async def _clear_interactive_msg(user_id: int, bot: Bot | None = None) -> None:
    """Clear tracked interactive message, delete from chat, and exit interactive mode."""
    msg_id = _interactive_msgs.pop(user_id, None)
    _interactive_mode.pop(user_id, None)
    if bot and msg_id:
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass  # Message may already be deleted or too old


def _get_interactive_window(user_id: int) -> str | None:
    """Get the window name for user's interactive mode."""
    return _interactive_mode.get(user_id)


# --- Streaming response / notifications ---


def _build_response_parts(
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
            handled = await _handle_interactive_ui(bot, user_id, wname)
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
            await _clear_interactive_msg(user_id, bot)

        parts = _build_response_parts(
            msg.text, msg.is_complete, msg.content_type, msg.role,
        )

        if msg.is_complete:
            # Enqueue content message task
            # Note: tool_result editing is handled inside _process_content_task
            # to ensure sequential processing with tool_use message sending
            await _enqueue_content_message(
                bot=bot,
                user_id=user_id,
                window_name=wname,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
                text=msg.text,
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


# --- App lifecycle ---


async def _status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all active users."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    while True:
        try:
            # Get all users with active sessions
            for user_id, wname in list(session_manager.active_sessions.items()):
                try:
                    # Skip terminal polling while content messages are pending
                    queue = _message_queues.get(user_id)
                    if queue and not queue.empty():
                        continue
                    await _update_status_message(bot, user_id, wname)
                except Exception as e:
                    logger.debug(f"Status update error for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)


async def post_init(application: Application) -> None:
    global session_monitor, _status_poll_task

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("start", "Show session menu"),
        BotCommand("list", "List all sessions"),
        BotCommand("history", "Message history for active session"),
        BotCommand("screenshot", "Capture terminal screenshot"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
    ]
    # Add Claude Code slash commands
    for cmd_name, desc in CC_COMMANDS.items():
        bot_commands.append(BotCommand(cmd_name, desc))

    await application.bot.set_my_commands(bot_commands)

    monitor = SessionMonitor()

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot)

    monitor.set_message_callback(message_callback)
    monitor.start()
    session_monitor = monitor
    logger.info("Session monitor started")

    # Start status polling task
    _status_poll_task = asyncio.create_task(_status_poll_loop(application.bot))
    logger.info("Status polling task started")


async def post_shutdown(application: Application) -> None:
    global _status_poll_task

    # Stop status polling
    if _status_poll_task:
        _status_poll_task.cancel()
        try:
            await _status_poll_task
        except asyncio.CancelledError:
            pass
        _status_poll_task = None
        logger.info("Status polling stopped")

    # Stop all queue workers
    for user_id, worker in list(_queue_workers.items()):
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
    _queue_workers.clear()
    _message_queues.clear()
    _queue_locks.clear()
    logger.info("Message queue workers stopped")

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")


async def _build_list_keyboard(
    user_id: int,
    pending_selection: str | None = None,
) -> InlineKeyboardMarkup:
    """Build inline keyboard with session buttons for /list.

    Args:
        user_id: User ID to check active window for.
        pending_selection: Override active window name for display (used during
            window switch to show checkmark before active_sessions is updated).
    """
    active_items = await session_manager.list_active_sessions()
    active_wname = pending_selection or session_manager.get_active_window_name(user_id)

    buttons: list[list[InlineKeyboardButton]] = []
    for w, session in active_items:
        is_active = active_wname == w.window_name
        check = "✅ " if is_active else ""
        summary = session.short_summary if session else "New session"
        label = f"{check}[{w.window_name}] {summary}"
        if len(label) > 40:
            label = label[:37] + "..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"{CB_LIST_SELECT}{w.window_name}"[:64])])

    buttons.append([InlineKeyboardButton("➕ New Session", callback_data=CB_LIST_NEW)])
    return InlineKeyboardMarkup(buttons)


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return application
