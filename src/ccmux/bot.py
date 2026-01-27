"""Telegram bot handlers for Claude Code session monitoring."""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
from .session import session_manager
from .session_monitor import NewMessage, SessionMonitor
from .telegram_sender import split_message
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Session monitor instance
session_monitor: SessionMonitor | None = None

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

# Bot's own commands — handled locally, NOT forwarded to Claude Code
BOT_COMMANDS = {"start", "list", "history", "cancel"}

# Claude Code slash commands shown in bot menu (command -> description)
CC_COMMANDS: dict[str, str] = {
    "clear": "Clear conversation history",
    "compact": "Compact conversation context",
    "cost": "Show token/cost usage",
    "help": "Show Claude Code help",
    "review": "Code review",
    "doctor": "Diagnose environment",
    "memory": "Edit CLAUDE.md",
    "init": "Init project CLAUDE.md",
}

# List inline callback prefixes
CB_LIST_SELECT = "ls:sel:"
CB_LIST_NEW = "ls:new"

# Directories per page in directory browser
DIRS_PER_PAGE = 6

# Messages per page in history view
MSGS_PER_PAGE = 5

# Max chars per message text in history
MSG_TEXT_MAX = 300

# User state keys
STATE_KEY = "state"
STATE_BROWSING_DIRECTORY = "browsing_directory"
BROWSE_PATH_KEY = "browse_path"
BROWSE_PAGE_KEY = "browse_page"


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


def _truncate(text: str, max_len: int = MSG_TEXT_MAX) -> str:
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


# --- Message history ---

def _build_history_keyboard(
    window_name: str, offset: int, total: int
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for history pagination."""
    total_pages = max(1, math.ceil(total / MSGS_PER_PAGE))
    current_page = (offset // MSGS_PER_PAGE) + 1

    if total_pages <= 1:
        return None

    # window_name fits callback data well (short, stable)
    buttons = []
    if current_page < total_pages:
        new_offset = offset + MSGS_PER_PAGE
        buttons.append(InlineKeyboardButton(
            "◀ Older",
            callback_data=f"{CB_HISTORY_PREV}{new_offset}:{window_name}"[:64],
        ))

    buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))

    if current_page > 1:
        new_offset = max(0, offset - MSGS_PER_PAGE)
        buttons.append(InlineKeyboardButton(
            "Newer ▶",
            callback_data=f"{CB_HISTORY_NEXT}{new_offset}:{window_name}"[:64],
        ))

    return InlineKeyboardMarkup([buttons])


async def send_history(
    target, window_name: str, offset: int = 0, edit: bool = False
) -> None:
    """Send or edit message history for a window's session.

    Args:
        target: Message object (for reply) or CallbackQuery (for edit).
        window_name: Tmux window name (resolved to session via sent messages).
        offset: Offset from end (0 = newest).
        edit: If True, edit existing message instead of sending new one.
    """
    messages, total = session_manager.get_recent_messages(
        window_name, count=MSGS_PER_PAGE, offset=offset,
    )

    # Resolve project name for display
    session = session_manager.resolve_session_for_window(window_name)
    project_name = Path(session.project_path).name if session else window_name

    if total == 0:
        text = f"📋 [{project_name}] No messages yet."
        keyboard = None
    else:
        end_pos = total - offset
        start_pos = end_pos - len(messages) + 1
        lines = [f"📋 [{project_name}] Messages ({start_pos}-{end_pos} of {total})\n"]
        for msg in messages:
            icon = "👤" if msg["role"] == "user" else "🤖"
            lines.append(f"{icon} {_truncate(msg['text'])}")
        text = "\n\n".join(lines)
        keyboard = _build_history_keyboard(window_name, offset, total)

    if edit:
        await target.edit_message_text(text, reply_markup=keyboard)
    else:
        await target.reply_text(text, reply_markup=keyboard)


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
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    if context.user_data:
        context.user_data.pop(STATE_KEY, None)
        context.user_data.pop(BROWSE_PATH_KEY, None)
        context.user_data.pop(BROWSE_PAGE_KEY, None)

    if update.message:
        # Remove any existing reply keyboard
        await update.message.reply_text(
            "🤖 *Claude Code Monitor*\n\n"
            "Use /list to see sessions.\n"
            "Send text to forward to the active session.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown",
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text

    # Ignore text in directory browsing mode
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY:
        await update.message.reply_text(
            "Please use the directory browser above, or tap Cancel."
        )
        return

    # Forward text to active window
    active_wname = session_manager.get_active_window_name(user.id)
    if active_wname:
        w = tmux_manager.find_window_by_name(active_wname)
        if not w:
            await update.message.reply_text(
                f"❌ Window '{active_wname}' no longer exists.\n"
                "Select a different session or create a new one."
            )
            return

        # Show typing indicator
        await update.message.chat.send_action(ChatAction.TYPING)

        success, message = session_manager.send_to_active_session(user.id, text)
        if success:
            # Send placeholder that will be edited with Claude's response
            project_name = Path(w.cwd).name
            placeholder = await update.message.reply_text(
                f"⏳ [{project_name}] waiting for response..."
            )
            _pending_responses[(active_wname, user.id)] = PendingResponse(
                chat_id=user.id,
                message_id=placeholder.message_id,
            )
        else:
            await update.message.reply_text(f"❌ {message}")
        return

    await update.message.reply_text(
        "❌ No active session selected.\n"
        "Use /list to select a session or create a new one."
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
        offset_str, window_name = rest.split(":", 1)
        offset = int(offset_str)

        w = tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(query, window_name, offset=offset, edit=True)
        else:
            await query.edit_message_text("Window no longer exists.")
        await query.answer("Page updated")

    # Directory browser handlers
    elif data.startswith(CB_DIR_SELECT):
        subdir_name = data[len(CB_DIR_SELECT):]
        default_path = str(config.browse_root_dir)
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        new_path = Path(current_path) / subdir_name

        if not new_path.exists() or not new_path.is_dir():
            await query.answer("Directory not found", show_alert=True)
            return

        new_path_str = str(new_path)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = new_path_str
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard = build_directory_browser(new_path_str)
        await query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
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
        await query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
        await query.answer()

    elif data.startswith(CB_DIR_PAGE):
        pg = int(data[len(CB_DIR_PAGE):])
        default_path = str(config.browse_root_dir)
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        if context.user_data is not None:
            context.user_data[BROWSE_PAGE_KEY] = pg

        msg_text, keyboard = build_directory_browser(current_path, pg)
        await query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
        await query.answer()

    elif data == CB_DIR_CONFIRM:
        default_path = str(config.browse_root_dir)
        selected_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path

        if context.user_data is not None:
            context.user_data.pop(STATE_KEY, None)
            context.user_data.pop(BROWSE_PATH_KEY, None)
            context.user_data.pop(BROWSE_PAGE_KEY, None)

        success, message = tmux_manager.create_window(selected_path)
        if success:
            resolved_path = str(Path(selected_path).expanduser().resolve())
            # Find the newly created window
            w = tmux_manager.find_window_by_cwd(resolved_path)
            if w:
                session_manager.set_active_window(user.id, w.window_name)

            await query.edit_message_text(
                f"✅ {message}\n\n_You can now send messages directly to this window._",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(f"❌ {message}", parse_mode="Markdown")
        await query.answer("Created" if success else "Failed")

    elif data == CB_DIR_CANCEL:
        if context.user_data is not None:
            context.user_data.pop(STATE_KEY, None)
            context.user_data.pop(BROWSE_PATH_KEY, None)
            context.user_data.pop(BROWSE_PAGE_KEY, None)
        await query.edit_message_text("Cancelled")
        await query.answer("Cancelled")

    # Session action: History
    elif data.startswith(CB_SESSION_HISTORY):
        window_name = data[len(CB_SESSION_HISTORY):]
        w = tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(query, window_name, offset=0, edit=True)
        else:
            await query.edit_message_text("Window no longer exists.")
        await query.answer("Loading history")

    # Session action: Refresh
    elif data.startswith(CB_SESSION_REFRESH):
        window_name = data[len(CB_SESSION_REFRESH):]
        session = session_manager.resolve_session_for_window(window_name)
        if session:
            project_name = Path(session.project_path).name
            detail_text = (
                f"📤 *Selected: {project_name}*\n\n"
                f"📝 {session.summary}\n"
                f"💬 {session.message_count} messages\n\n"
                f"Send text to forward to Claude."
            )
            action_buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 History", callback_data=f"{CB_SESSION_HISTORY}{window_name}"[:64]),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"{CB_SESSION_REFRESH}{window_name}"[:64]),
                InlineKeyboardButton("❌ Kill", callback_data=f"{CB_SESSION_KILL}{window_name}"[:64]),
            ]])
            await query.edit_message_text(detail_text, reply_markup=action_buttons, parse_mode="Markdown")
        else:
            await query.edit_message_text("Session no longer exists.")
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
            await query.edit_message_text(f"🗑 Session killed.")
        else:
            await query.edit_message_text("Window already gone.")
        await query.answer("Killed")

    # List: select session
    elif data.startswith(CB_LIST_SELECT):
        wname = data[len(CB_LIST_SELECT):]
        w = tmux_manager.find_window_by_name(wname) if wname else None
        if w:
            session_manager.set_active_window(user.id, w.window_name)
            # Re-render list with updated checkmark
            sessions = session_manager.list_active_sessions()
            text = f"📊 {len(sessions)} active sessions:"
            keyboard = _build_list_keyboard(user.id)
            await query.edit_message_text(text, reply_markup=keyboard)
            # Send session detail message
            session = session_manager.resolve_session_for_window(w.window_name)
            project_name = Path(w.cwd).name
            if session:
                detail_text = (
                    f"📤 *Selected: {project_name}*\n\n"
                    f"📝 {session.summary}\n"
                    f"💬 {session.message_count} messages\n\n"
                    f"Send text to forward to Claude."
                )
            else:
                detail_text = f"📤 *Selected: {project_name}*\n\nSend text to forward to Claude."
            action_buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 History", callback_data=f"{CB_SESSION_HISTORY}{w.window_name}"[:64]),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"{CB_SESSION_REFRESH}{w.window_name}"[:64]),
                InlineKeyboardButton("❌ Kill", callback_data=f"{CB_SESSION_KILL}{w.window_name}"[:64]),
            ]])
            await context.bot.send_message(
                chat_id=user.id,
                text=detail_text,
                reply_markup=action_buttons,
                parse_mode="Markdown",
            )
            await query.answer(f"Active: {project_name}")
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
        await query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
        await query.answer()

    elif data == "noop":
        await query.answer()


# --- Streaming response / notifications ---

@dataclass
class PendingResponse:
    """A placeholder Telegram message waiting for Claude's response."""
    chat_id: int
    message_id: int


# (window_name, user_id) -> PendingResponse
_pending_responses: dict[tuple[str, int], PendingResponse] = {}


def _format_response_prefix(
    project_path: str, is_complete: bool, content_type: str = "text",
) -> str:
    """Return the emoji + project prefix for a response."""
    project_name = Path(project_path).name
    if content_type == "thinking":
        return f"💭 [{project_name}]"
    if is_complete:
        return f"🤖 [{project_name}]"
    return f"⏳ [{project_name}]"


def _build_response_parts(
    project_path: str, text: str, is_complete: bool,
    content_type: str = "text",
) -> list[str]:
    """Build paginated response messages for Telegram.

    Returns a list of message strings, each within Telegram's 4096 char limit.
    Multi-part messages get a [1/N] suffix.
    """
    prefix = _format_response_prefix(project_path, is_complete, content_type)

    # Truncate thinking content to keep it compact
    if content_type == "thinking" and is_complete:
        max_thinking = 500
        if len(text) > max_thinking:
            text = text[:max_thinking] + "\n\n... (thinking truncated)"

    max_text = 4000 - len(prefix)

    text_chunks = split_message(text, max_length=max_text)
    total = len(text_chunks)

    if total == 1:
        return [f"{prefix}\n\n{text_chunks[0]}"]

    parts = []
    for i, chunk in enumerate(text_chunks, 1):
        parts.append(f"{prefix}\n\n{chunk}\n\n[{i}/{total}]")
    return parts


async def handle_new_message(msg: NewMessage, bot: Bot) -> None:
    """Handle a new assistant message — edit placeholder or send new message.

    For streaming: edits the pending placeholder in-place.
    For complete: finalizes the message (or sends new if no placeholder).
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

    parts = _build_response_parts(
        msg.project_path, msg.text, msg.is_complete, msg.content_type,
    )

    for user_id, wname in active_users:
        key = (wname, user_id)
        pending = _pending_responses.get(key)
        if pending:
            if msg.is_complete:
                # Delete the ⏳ placeholder and send final parts
                try:
                    await bot.delete_message(
                        chat_id=pending.chat_id,
                        message_id=pending.message_id,
                    )
                except Exception as e:
                    logger.debug(f"Failed to delete placeholder: {e}")
                del _pending_responses[key]
                for part in parts:
                    try:
                        await bot.send_message(chat_id=user_id, text=part)
                    except Exception as e:
                        logger.error(f"Failed to send complete message: {e}")
            else:
                # Streaming: edit the placeholder in-place (first part only)
                try:
                    await bot.edit_message_text(
                        chat_id=pending.chat_id,
                        message_id=pending.message_id,
                        text=parts[0],
                    )
                except Exception as e:
                    err_msg = str(e).lower()
                    if "not modified" not in err_msg:
                        logger.warning(f"Failed to edit pending message: {e}")
        else:
            # No placeholder — send new message (unsolicited response)
            if msg.is_complete:
                for part in parts:
                    try:
                        await bot.send_message(chat_id=user_id, text=part)
                    except Exception as e:
                        logger.error(f"Failed to send notification to {user_id}: {e}")


# --- App lifecycle ---

async def post_init(application: Application) -> None:
    global session_monitor

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("start", "Show session menu"),
        BotCommand("list", "List all sessions"),
        BotCommand("history", "Message history for active session"),
        BotCommand("cancel", "Cancel current operation"),
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


async def post_shutdown(application: Application) -> None:
    global session_monitor
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
        await update.message.reply_text(
            "❌ No active session. Select a session first."
        )
        return

    w = tmux_manager.find_window_by_name(active_wname)
    if not w:
        await update.message.reply_text(
            f"❌ Window '{active_wname}' no longer exists."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    success, message = session_manager.send_to_active_session(user.id, cc_slash)
    if success:
        project_name = Path(w.cwd).name
        await update.message.reply_text(
            f"⚡ [{project_name}] Sent: {cc_slash}"
        )
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            session_manager.clear_window_session(active_wname)
    else:
        await update.message.reply_text(f"❌ {message}")


def _build_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard with session buttons for /list."""
    sessions = session_manager.list_active_sessions()
    active_wname = session_manager.get_active_window_name(user_id)

    buttons: list[list[InlineKeyboardButton]] = []
    for s in sessions:
        w = session_manager.find_window_for_project(s.project_path)
        is_active = w is not None and active_wname == w.window_name
        wname = w.window_name if w else ""

        check = "✅ " if is_active else ""
        label = f"{check}[{s.project_name}] {s.short_summary}"
        if len(label) > 40:
            label = label[:37] + "..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"{CB_LIST_SELECT}{wname}"[:64])])

    buttons.append([InlineKeyboardButton("➕ New Session", callback_data=CB_LIST_NEW)])
    return InlineKeyboardMarkup(buttons)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active sessions as inline buttons."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    sessions = session_manager.list_active_sessions()
    text = f"📊 {len(sessions)} active sessions:" if sessions else "No active sessions."
    keyboard = _build_list_keyboard(user.id)

    await update.message.reply_text(text, reply_markup=keyboard)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await update.message.reply_text("❌ No active session. Select one first.")
        return

    await send_history(update.message, active_wname)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return

    if context.user_data:
        context.user_data.pop(STATE_KEY, None)
        context.user_data.pop(BROWSE_PATH_KEY, None)
        context.user_data.pop(BROWSE_PAGE_KEY, None)

    if update.message:
        await update.message.reply_text("Cancelled.")


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
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return application
