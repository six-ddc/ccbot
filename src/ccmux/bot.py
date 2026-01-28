"""Telegram bot handlers for Claude Code session monitoring."""

import asyncio
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
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
from .markdown_v2 import convert_markdown
from .screenshot import text_to_image
from .session import session_manager
from .session_monitor import NewMessage, SessionMonitor
from .telegram_sender import split_message
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Session monitor instance
session_monitor: SessionMonitor | None = None

# Status polling task
_status_poll_task: asyncio.Task | None = None
STATUS_POLL_INTERVAL = 1.0  # seconds

# Map (tool_use_id, user_id) -> telegram message_id for editing tool_use messages with results
_tool_msg_ids: dict[tuple[str, int], int] = {}

# Status message tracking: user_id -> (message_id, window_name, last_text)
# Note: last_text may be missing in old entries during rolling update
_status_msg_info: dict[int, tuple[int, str] | tuple[int, str, str]] = {}

# Claude Code spinner characters that indicate status line
STATUS_SPINNERS = frozenset(["·", "✻", "✽", "✶", "✳", "✢"])


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
    is_complete: bool = True


# Per-user message queues and worker tasks
_message_queues: dict[int, asyncio.Queue[MessageTask]] = {}
_queue_workers: dict[int, asyncio.Task[None]] = {}


def _get_or_create_queue(bot: Bot, user_id: int) -> asyncio.Queue[MessageTask]:
    """Get or create message queue and worker for a user."""
    if user_id not in _message_queues:
        _message_queues[user_id] = asyncio.Queue()
        # Start worker task for this user
        _queue_workers[user_id] = asyncio.create_task(
            _message_queue_worker(bot, user_id)
        )
    return _message_queues[user_id]


async def _message_queue_worker(bot: Bot, user_id: int) -> None:
    """Process message tasks for a user sequentially."""
    queue = _message_queues[user_id]
    logger.info(f"Message queue worker started for user {user_id}")

    while True:
        try:
            task = await queue.get()
            try:
                if task.task_type == "content":
                    await _process_content_task(bot, user_id, task)
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

    # 1. Handle tool_result editing (lookup happens here to ensure sequential order)
    if task.content_type == "tool_result" and task.tool_use_id:
        _tkey = (task.tool_use_id, user_id)
        edit_msg_id = _tool_msg_ids.pop(_tkey, None)
        if edit_msg_id is not None:
            # Clear status message first (tool_result edits a different message)
            # Don't remove keyboard - _check_and_send_status will send new status with keyboard
            await _do_clear_status_message(bot, user_id)
            text_md = convert_markdown(f"{_format_response_prefix(wname, True)}\n\n{task.text}")
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=edit_msg_id,
                    text=text_md,
                    parse_mode="MarkdownV2",
                )
                # After content, check and send status
                await _check_and_send_status(bot, user_id, wname)
                return
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=edit_msg_id,
                        text=f"{_format_response_prefix(wname, True)}\n\n{task.text}",
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
                # Status message was edited to show content
                last_msg_id = converted_msg_id
                continue

        try:
            sent = await bot.send_message(
                chat_id=user_id, text=part, parse_mode="MarkdownV2"
            )
        except Exception:
            try:
                sent = await bot.send_message(chat_id=user_id, text=part)
            except Exception as e:
                logger.error(f"Failed to send message to {user_id}: {e}")

        if sent:
            last_msg_id = sent.message_id

    # Record tool_use message ID for later editing (use last message sent)
    if last_msg_id and task.tool_use_id and task.content_type == "tool_use":
        _tool_msg_ids[(task.tool_use_id, user_id)] = last_msg_id

    # 3. After content, check and send status
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
        )
        return msg_id
    except Exception:
        try:
            # Fallback to plain text
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=msg_id,
                text=content_text,
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
                )
                _status_msg_info[user_id] = (msg_id, wname, status_text)
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=status_text,
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
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
        )
        _status_msg_info[user_id] = (sent.message_id, window_name, text)
    except Exception:
        try:
            sent = await bot.send_message(chat_id=user_id, text=text)
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
    w = tmux_manager.find_window_by_name(window_name)
    if not w:
        return

    pane_text = tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return

    status_line = _parse_status_line(pane_text)
    if status_line:
        await _do_send_status_message(bot, user_id, window_name, status_line)

# Callback data prefixes
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer

# Directory browser callback prefixes
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"

# Session action callback prefixes
CB_SESSION_HISTORY = "sa:hist:"
CB_SESSION_REFRESH = "sa:ref:"
CB_SESSION_KILL = "sa:kill:"

# Screenshot callback prefix
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Bot's own commands — handled locally, NOT forwarded to Claude Code
BOT_COMMANDS = {"start", "list", "history", "screenshot", "esc"}

# Claude Code commands shown in bot menu (forwarded via tmux)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
    "memory": "↗ Edit CLAUDE.md",
}

# List inline callback prefixes
CB_LIST_SELECT = "ls:sel:"
CB_LIST_NEW = "ls:new"

# Directories per page in directory browser
DIRS_PER_PAGE = 6


# User state keys
STATE_KEY = "state"
STATE_BROWSING_DIRECTORY = "browsing_directory"
BROWSE_PATH_KEY = "browse_path"
BROWSE_PAGE_KEY = "browse_page"


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


def _clear_browse_state(user_data: dict | None) -> None:
    """Clear directory browsing state keys from user_data."""
    if user_data is not None:
        user_data.pop(STATE_KEY, None)
        user_data.pop(BROWSE_PATH_KEY, None)
        user_data.pop(BROWSE_PAGE_KEY, None)


def _build_session_detail(
    window_name: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build session detail text and action buttons for a window."""
    session = session_manager.resolve_session_for_window(window_name)
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
    try:
        return await message.reply_text(
            convert_markdown(text), parse_mode="MarkdownV2", **kwargs,
        )
    except Exception:
        return await message.reply_text(text, **kwargs)


async def _safe_edit(target, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Edit message with MarkdownV2, falling back to plain text on failure."""
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
    window_name: str, page_index: int, total_pages: int
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for history pagination."""
    if total_pages <= 1:
        return None

    buttons = []
    if page_index > 0:
        buttons.append(InlineKeyboardButton(
            "◀ Older",
            callback_data=f"{CB_HISTORY_PREV}{page_index - 1}:{window_name}"[:64],
        ))

    buttons.append(InlineKeyboardButton(f"{page_index + 1}/{total_pages}", callback_data="noop"))

    if page_index < total_pages - 1:
        buttons.append(InlineKeyboardButton(
            "Newer ▶",
            callback_data=f"{CB_HISTORY_NEXT}{page_index + 1}:{window_name}"[:64],
        ))

    return InlineKeyboardMarkup([buttons])


async def send_history(
    target, window_name: str, offset: int = -1, edit: bool = False
) -> None:
    """Send or edit message history for a window's session.

    Args:
        target: Message object (for reply) or CallbackQuery (for edit).
        window_name: Tmux window name (resolved to session via sent messages).
        offset: Page index (0-based). -1 means last page.
        edit: If True, edit existing message instead of sending new one.
    """
    messages, total = session_manager.get_recent_messages(
        window_name, count=0,
    )

    if total == 0:
        text = f"📋 [{window_name}] No messages yet."
        keyboard = None
    else:
        from .transcript_parser import TranscriptParser
        _start = TranscriptParser.EXPANDABLE_QUOTE_START
        _end = TranscriptParser.EXPANDABLE_QUOTE_END

        lines = [f"📋 [{window_name}] Messages ({total} total)\n"]
        for msg in messages:
            if msg["role"] == "user":
                icon = "👤"
            elif msg.get("content_type") == "thinking":
                icon = "💭"
            else:
                icon = "🤖"
            msg_text = msg["text"]
            # Strip expandable quote sentinels for history view —
            # content is shown inline, not as collapsed blocks.
            msg_text = msg_text.replace(_start, "").replace(_end, "")
            lines.append(f"{icon} {msg_text}")
        full_text = "\n\n".join(lines)
        pages = split_message(full_text, max_length=4096)
        # Default to last page (newest messages), navigate backwards
        if offset < 0:
            offset = len(pages) - 1
        page_index = max(0, min(offset, len(pages) - 1))
        text = pages[page_index]
        keyboard = _build_history_keyboard(window_name, page_index, len(pages))

    if edit:
        await _safe_edit(target, text, reply_markup=keyboard)
    else:
        await _safe_reply(target, text, reply_markup=keyboard)


# --- Directory browser ---

def build_directory_browser(current_path: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    path = Path(current_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        path = config.browse_root_dir

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
        for name in page_dirs[i:i+2]:
            display = name[:12] + "…" if len(name) > 13 else name
            row.append(InlineKeyboardButton(f"📁 {display}", callback_data=f"{CB_DIR_SELECT}{name}"))
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
    browse_root = config.browse_root_dir.resolve()
    if path != path.parent and path != browse_root:
        action_row.append(InlineKeyboardButton("Up", callback_data=CB_DIR_UP))
    action_row.append(InlineKeyboardButton("Select", callback_data=CB_DIR_CONFIRM))
    action_row.append(InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL))
    buttons.append(action_row)

    display_path = str(path).replace(str(Path.home()), "~")
    if not subdirs:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\n_(No subdirectories)_"
    else:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\nTap a folder to enter, or select current directory"

    return text, InlineKeyboardMarkup(buttons)


# --- Command / message handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await _safe_reply(update.message, "You are not authorized to use this bot.")
        return

    _clear_browse_state(context.user_data)

    if update.message:
        # Remove any existing reply keyboard
        await _safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Use /list to see sessions.\n"
            "Send text to forward to the active session.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await _safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text

    # Ignore text in directory browsing mode
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY:
        await _safe_reply(
            update.message,
            "Please use the directory browser above, or tap Cancel.",
        )
        return

    # Forward text to active window
    active_wname = session_manager.get_active_window_name(user.id)
    if active_wname:
        w = tmux_manager.find_window_by_name(active_wname)
        if not w:
            await _safe_reply(
                update.message,
                f"❌ Window '{active_wname}' no longer exists.\n"
                "Select a different session or create a new one.",
            )
            return

        # Show typing indicator while waiting for Claude's response
        await update.message.chat.send_action(ChatAction.TYPING)

        # Clear status message tracking so next status update sends a new message
        # (otherwise it would edit the old status message above user's message)
        _status_msg_info.pop(user.id, None)

        success, message = session_manager.send_to_active_session(user.id, text)
        if not success:
            await _safe_reply(update.message, f"❌ {message}")
        return

    await _safe_reply(
        update.message,
        "❌ No active session selected.\n"
        "Use /list to select a session or create a new one.",
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        await query.answer("Not authorized")
        return

    data = query.data

    # History: older
    if data.startswith(CB_HISTORY_PREV) or data.startswith(CB_HISTORY_NEXT):
        prefix_len = len(CB_HISTORY_PREV)  # same length for both
        rest = data[prefix_len:]
        try:
            offset_str, window_name = rest.split(":", 1)
            offset = int(offset_str)
        except ValueError:
            await query.answer("Invalid data")
            return

        w = tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(query, window_name, offset=offset, edit=True)
        else:
            await _safe_edit(query, "Window no longer exists.")
        await query.answer("Page updated")

    # Directory browser handlers
    elif data.startswith(CB_DIR_SELECT):
        subdir_name = data[len(CB_DIR_SELECT):]
        default_path = str(config.browse_root_dir)
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        new_path = (Path(current_path) / subdir_name).resolve()

        # Validate: must be within browse_root_dir (prevent path traversal)
        browse_root = config.browse_root_dir.resolve()
        if not (str(new_path).startswith(str(browse_root) + "/") or new_path == browse_root):
            await query.answer("Access denied", show_alert=True)
            return

        if not new_path.exists() or not new_path.is_dir():
            await query.answer("Directory not found", show_alert=True)
            return

        new_path_str = str(new_path)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = new_path_str
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard = build_directory_browser(new_path_str)
        await _safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_UP:
        default_path = str(config.browse_root_dir)
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        current = Path(current_path).resolve()
        parent = current.parent
        root = config.browse_root_dir.resolve()
        if not str(parent).startswith(str(root)) and parent != root:
            parent = root

        parent_path = str(parent)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = parent_path
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard = build_directory_browser(parent_path)
        await _safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data.startswith(CB_DIR_PAGE):
        try:
            pg = int(data[len(CB_DIR_PAGE):])
        except ValueError:
            await query.answer("Invalid data")
            return
        default_path = str(config.browse_root_dir)
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        if context.user_data is not None:
            context.user_data[BROWSE_PAGE_KEY] = pg

        msg_text, keyboard = build_directory_browser(current_path, pg)
        await _safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_CONFIRM:
        default_path = str(config.browse_root_dir)
        selected_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path

        _clear_browse_state(context.user_data)

        success, message, created_wname = tmux_manager.create_window(selected_path)
        if success:
            session_manager.set_active_window(user.id, created_wname)

            await _safe_edit(
                query,
                f"✅ {message}\n\n_You can now send messages directly to this window._",
            )
        else:
            await _safe_edit(query, f"❌ {message}")
        await query.answer("Created" if success else "Failed")

    elif data == CB_DIR_CANCEL:
        _clear_browse_state(context.user_data)
        await _safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Session action: History
    elif data.startswith(CB_SESSION_HISTORY):
        window_name = data[len(CB_SESSION_HISTORY):]
        w = tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(query.message, window_name)
        else:
            await _safe_edit(query, "Window no longer exists.")
        await query.answer("Loading history")

    # Session action: Refresh
    elif data.startswith(CB_SESSION_REFRESH):
        window_name = data[len(CB_SESSION_REFRESH):]
        detail_text, action_buttons = _build_session_detail(window_name)
        await _safe_edit(query, detail_text, reply_markup=action_buttons)
        await query.answer("Refreshed")

    # Session action: Kill
    elif data.startswith(CB_SESSION_KILL):
        window_name = data[len(CB_SESSION_KILL):]
        w = tmux_manager.find_window_by_name(window_name)
        if w:
            tmux_manager.kill_window(w.window_id)
            # Clear active session if it was this one
            if user:
                active_wname = session_manager.get_active_window_name(user.id)
                if active_wname == window_name:
                    session_manager.set_active_window(user.id, "")
            await _safe_edit(query, "🗑 Session killed.")
        else:
            await _safe_edit(query, "Window already gone.")
        await query.answer("Killed")

    # Screenshot: Refresh
    elif data.startswith(CB_SCREENSHOT_REFRESH):
        window_name = data[len(CB_SCREENSHOT_REFRESH):]
        w = tmux_manager.find_window_by_name(window_name)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            return

        text = tmux_manager.capture_pane(w.window_id)
        if not text:
            await query.answer("Failed to capture pane", show_alert=True)
            return

        png_bytes = text_to_image(text)
        refresh_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data=f"{CB_SCREENSHOT_REFRESH}{window_name}"[:64]),
        ]])
        try:
            await query.edit_message_media(
                media=InputMediaDocument(media=io.BytesIO(png_bytes), filename="screenshot.png"),
                reply_markup=refresh_keyboard,
            )
            await query.answer("Refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh screenshot: {e}")
            await query.answer("Failed to refresh", show_alert=True)

    # List: select session
    elif data.startswith(CB_LIST_SELECT):
        wname = data[len(CB_LIST_SELECT):]
        w = tmux_manager.find_window_by_name(wname) if wname else None
        if w:
            session_manager.set_active_window(user.id, w.window_name)
            # Re-render list with updated checkmark
            active_items = session_manager.list_active_sessions()
            text = f"📊 {len(active_items)} active sessions:"
            keyboard = _build_list_keyboard(user.id)
            await _safe_edit(query, text, reply_markup=keyboard)
            # Send session detail message
            detail_text, action_buttons = _build_session_detail(w.window_name)
            await _safe_send(
                context.bot, user.id, detail_text,
                reply_markup=action_buttons,
            )
            await query.answer(f"Active: {w.window_name}")
        else:
            await query.answer("Window no longer exists", show_alert=True)

    # List: new session
    elif data == CB_LIST_NEW:
        start_path = str(config.browse_root_dir)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
        msg_text, keyboard = build_directory_browser(start_path)
        await _safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == "noop":
        await query.answer()


# --- Status line polling ---


def _parse_status_line(pane_text: str) -> str | None:
    """Extract the Claude Code status line from terminal output.

    Returns the status text if found, None otherwise.
    Status lines start with spinner characters: · ✻ ✽ ✶ ✳ ✢
    """
    if not pane_text:
        return None

    # Search from bottom up - status line can be anywhere in last ~15 lines
    # (there may be separator lines, prompts, etc. below it)
    lines = pane_text.strip().split("\n")
    for line in reversed(lines[-15:]):
        line = line.strip()
        if not line:
            continue
        # Check if line starts with a spinner character
        first_char = line[0] if line else ""
        if first_char in STATUS_SPINNERS:
            return line
    return None


def _enqueue_status_update(bot: Bot, user_id: int, window_name: str, status_text: str | None) -> None:
    """Enqueue a status update task."""
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
    """Poll terminal and enqueue status update for user's active window."""
    w = tmux_manager.find_window_by_name(window_name)
    if not w:
        # Window gone, enqueue clear
        _enqueue_status_update(bot, user_id, window_name, None)
        return

    pane_text = tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # No pane content, enqueue clear
        _enqueue_status_update(bot, user_id, window_name, None)
        return

    status_line = _parse_status_line(pane_text)
    current_info = _status_msg_info.get(user_id)

    if status_line:
        _enqueue_status_update(bot, user_id, window_name, status_line)
    elif current_info:
        # No status line but we have a status message, clear it
        _enqueue_status_update(bot, user_id, window_name, None)


def _enqueue_content_message(
    bot: Bot,
    user_id: int,
    window_name: str,
    parts: list[str],
    tool_use_id: str | None = None,
    content_type: str = "text",
    is_complete: bool = True,
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
        is_complete=is_complete,
    )
    queue.put_nowait(task)


# --- Streaming response / notifications ---


def _format_response_prefix(
    window_name: str, is_complete: bool, content_type: str = "text",
) -> str:
    """Return the emoji + window prefix for a response."""
    if content_type == "thinking":
        return f"💭 [{window_name}]"
    if is_complete:
        return f"🤖 [{window_name}]"
    return f"⏳ [{window_name}]"


def _build_response_parts(
    window_name: str, text: str, is_complete: bool,
    content_type: str = "text",
) -> list[str]:
    """Build paginated response messages for Telegram.

    Returns a list of message strings, each within Telegram's 4096 char limit.
    Multi-part messages get a [1/N] suffix.
    """
    text = text.strip()
    prefix = _format_response_prefix(window_name, is_complete, content_type)

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

    # If text contains expandable quote sentinels, don't split —
    # the quote must stay atomic. Truncation is handled by
    # _render_expandable_quote in markdown_v2.py.
    from .transcript_parser import TranscriptParser
    if TranscriptParser.EXPANDABLE_QUOTE_START in text:
        return [convert_markdown(f"{prefix}\n\n{text}")]

    # Split markdown first, then convert each chunk to HTML.
    # Use conservative max to leave room for HTML tags added by conversion.
    max_text = 3000 - len(prefix)

    text_chunks = split_message(text, max_length=max_text)
    total = len(text_chunks)

    if total == 1:
        return [convert_markdown(f"{prefix}\n\n{text_chunks[0]}")]

    parts = []
    for i, chunk in enumerate(text_chunks, 1):
        parts.append(convert_markdown(f"{prefix}\n\n{chunk}\n\n[{i}/{total}]"))
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
        resolved = session_manager.resolve_session_for_window(wname)
        if resolved and resolved.session_id == msg.session_id:
            active_users.append((uid, wname))

    if not active_users:
        logger.info(
            f"No active users for session {msg.session_id}. "
            f"Active sessions: {dict(session_manager.active_sessions)}"
        )
        # Log what each active user resolves to, for debugging
        for uid, wname in session_manager.active_sessions.items():
            resolved = session_manager.resolve_session_for_window(wname)
            resolved_id = resolved.session_id if resolved else None
            logger.info(
                f"  user={uid}, window={wname} -> resolved_session={resolved_id}"
            )
        return

    for user_id, wname in active_users:
        parts = _build_response_parts(
            wname, msg.text, msg.is_complete, msg.content_type,
        )

        if msg.is_complete:
            # Enqueue content message task
            # Note: tool_result editing is handled inside _process_content_task
            # to ensure sequential processing with tool_use message sending
            _enqueue_content_message(
                bot=bot,
                user_id=user_id,
                window_name=wname,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
                is_complete=True,
                text=msg.text,
            )


# --- App lifecycle ---


async def _status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all active users."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    while True:
        try:
            # Get all users with active sessions
            for user_id, wname in list(session_manager.active_sessions.items()):
                try:
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
    global session_monitor, _status_poll_task

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
    logger.info("Message queue workers stopped")

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")


async def forward_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    cmd_text = update.message.text or ""
    # The full text is already a slash command like "/clear" or "/compact foo"
    cc_slash = cmd_text.split("@")[0]  # strip bot mention

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await _safe_reply(update.message, "❌ No active session. Select a session first.")
        return

    w = tmux_manager.find_window_by_name(active_wname)
    if not w:
        await _safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    success, message = session_manager.send_to_active_session(user.id, cc_slash)
    if success:
        await _safe_reply(update.message, f"⚡ [{active_wname}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            session_manager.clear_window_session(active_wname)
    else:
        await _safe_reply(update.message, f"❌ {message}")


def _build_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard with session buttons for /list."""
    active_items = session_manager.list_active_sessions()
    active_wname = session_manager.get_active_window_name(user_id)

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


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active sessions as inline buttons."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_items = session_manager.list_active_sessions()
    text = f"📊 {len(active_items)} active sessions:" if active_items else "No active sessions."
    keyboard = _build_list_keyboard(user.id)

    await _safe_reply(update.message, text, reply_markup=keyboard)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await _safe_reply(update.message, "❌ No active session. Select one first.")
        return

    await send_history(update.message, active_wname)


async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture the current tmux pane and send it as an image."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await _safe_reply(update.message, "❌ No active session. Select one first.")
        return

    w = tmux_manager.find_window_by_name(active_wname)
    if not w:
        await _safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    text = tmux_manager.capture_pane(w.window_id)
    if not text:
        await _safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = text_to_image(text)
    refresh_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"{CB_SCREENSHOT_REFRESH}{active_wname}"[:64]),
    ]])
    await update.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=refresh_keyboard,
    )


async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Escape key to interrupt Claude."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await _safe_reply(update.message, "❌ No active session. Select one first.")
        return

    w = tmux_manager.find_window_by_name(active_wname)
    if not w:
        await _safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    # Send Escape control character (no enter)
    tmux_manager.send_keys(w.window_id, "\x1b", enter=False)
    await _safe_reply(update.message, "⎋ Sent Escape")



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
