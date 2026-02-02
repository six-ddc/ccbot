"""Telegram bot handlers — the main UI layer of CCMux.

Registers all command/callback/message handlers and manages the bot lifecycle.
Core responsibilities:
  - Command handlers: /start, /list, /history, /screenshot, /esc, plus
    forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: session selection, directory browser, history
    pagination, interactive UI navigation, screenshot refresh.
  - Bot lifecycle management: post_init, post_shutdown, create_bot.

Handler modules (in handlers/):
  - callback_data: Callback data constants
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers
  - history: Message history pagination
  - directory_browser: Directory browser UI
  - interactive_ui: Interactive UI handling
  - status_polling: Terminal status polling
  - response_builder: Response message building

Key functions: create_bot(), handle_new_message().
"""

import asyncio
import io
import logging
from pathlib import Path

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
from .handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_UP,
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_LIST_NEW,
    CB_LIST_SELECT,
    CB_SCREENSHOT_REFRESH,
    CB_SESSION_HISTORY,
    CB_SESSION_KILL,
    CB_SESSION_REFRESH,
)
from .handlers.directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    build_directory_browser,
    clear_browse_state,
)
from .handlers.history import send_history
from .handlers.interactive_ui import (
    INTERACTIVE_TOOL_NAMES,
    clear_interactive_msg,
    get_interactive_msg_id,
    get_interactive_window,
    handle_interactive_ui,
    set_interactive_mode,
)
from .handlers.message_queue import (
    clear_status_msg_info,
    enqueue_content_message,
    get_message_queue,
    get_or_create_queue,
    shutdown_workers,
)
from .handlers.message_sender import NO_LINK_PREVIEW, safe_edit, safe_reply, safe_send
from .handlers.response_builder import build_response_parts
from .handlers.status_polling import status_poll_loop
from .screenshot import text_to_image
from .session import session_manager
from .session_monitor import NewMessage, SessionMonitor
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Session monitor instance
session_monitor: SessionMonitor | None = None

# Status polling task
_status_poll_task: asyncio.Task | None = None

# Claude Code commands shown in bot menu (forwarded via tmux)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
    "memory": "↗ Edit CLAUDE.md",
}


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


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


# --- Command handlers ---


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    clear_browse_state(context.user_data)

    if update.message:
        # Remove any existing reply keyboard
        await safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Use /list to see sessions.\n"
            "Send text to forward to the active session.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active sessions as inline buttons."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_items = await session_manager.list_active_sessions()
    text = f"📊 {len(active_items)} active sessions:" if active_items else "No active sessions."
    keyboard = await _build_list_keyboard(user.id)

    await safe_reply(update.message, text, reply_markup=keyboard)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    active_wname = session_manager.get_active_window_name(user.id)
    if not active_wname:
        await safe_reply(update.message, "❌ No active session. Select one first.")
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
        await safe_reply(update.message, "❌ No active session. Select one first.")
        return

    w = await tmux_manager.find_window_by_name(active_wname)
    if not w:
        await safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)
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
        await safe_reply(update.message, "❌ No active session. Select one first.")
        return

    w = await tmux_manager.find_window_by_name(active_wname)
    if not w:
        await safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    # Send Escape control character (no enter)
    await tmux_manager.send_keys(w.window_id, "\x1b", enter=False)
    await safe_reply(update.message, "⎋ Sent Escape")


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
        await safe_reply(update.message, "❌ No active session. Select a session first.")
        return

    w = await tmux_manager.find_window_by_name(active_wname)
    if not w:
        await safe_reply(update.message, f"❌ Window '{active_wname}' no longer exists.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    success, message = await session_manager.send_to_active_session(user.id, cc_slash)
    if success:
        await safe_reply(update.message, f"⚡ [{active_wname}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            session_manager.clear_window_session(active_wname)
    else:
        await safe_reply(update.message, f"❌ {message}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text

    # Ignore text in directory browsing mode
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY:
        await safe_reply(
            update.message,
            "Please use the directory browser above, or tap Cancel.",
        )
        return

    # Forward text to active window
    active_wname = session_manager.get_active_window_name(user.id)
    if active_wname:
        w = await tmux_manager.find_window_by_name(active_wname)
        if not w:
            await safe_reply(
                update.message,
                f"❌ Window '{active_wname}' no longer exists.\n"
                "Select a different session or create a new one.",
            )
            return

        # Show typing indicator while waiting for Claude's response
        await update.message.chat.send_action(ChatAction.TYPING)

        # Clear status message tracking so next status update sends a new message
        # (otherwise it would edit the old status message above user's message)
        clear_status_msg_info(user.id)

        success, message = await session_manager.send_to_active_session(user.id, text)
        if not success:
            await safe_reply(update.message, f"❌ {message}")
            return

        # If in interactive mode, refresh the UI after sending text
        interactive_window = get_interactive_window(user.id)
        if interactive_window and interactive_window == active_wname:
            await asyncio.sleep(0.2)  # Wait for terminal to update
            await handle_interactive_ui(context.bot, user.id, active_wname)
        return

    await safe_reply(
        update.message,
        "❌ No active session selected.\n"
        "Use /list to select a session or create a new one.",
    )


# --- Callback query handler ---


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        await query.answer("Not authorized")
        return

    data = query.data

    # History: older/newer pagination
    # Format: hp:<page>:<window>:<start>:<end> or hn:<page>:<window>:<start>:<end>
    if data.startswith(CB_HISTORY_PREV) or data.startswith(CB_HISTORY_NEXT):
        prefix_len = len(CB_HISTORY_PREV)  # same length for both
        rest = data[prefix_len:]
        try:
            parts = rest.split(":")
            if len(parts) < 4:
                # Old format without byte range: page:window
                offset_str, window_name = rest.split(":", 1)
                start_byte, end_byte = 0, 0
            else:
                # New format: page:window:start:end (window may contain colons)
                offset_str = parts[0]
                start_byte = int(parts[-2])
                end_byte = int(parts[-1])
                window_name = ":".join(parts[1:-2])
            offset = int(offset_str)
        except (ValueError, IndexError):
            await query.answer("Invalid data")
            return

        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(
                query,
                window_name,
                offset=offset,
                edit=True,
                start_byte=start_byte,
                end_byte=end_byte,
                # Don't pass user_id for pagination - offset update only on initial view
                # This prevents offset from going backwards if new messages arrive while paging
            )
        else:
            await safe_edit(query, "Window no longer exists.")
        await query.answer("Page updated")

    # Directory browser handlers
    elif data.startswith(CB_DIR_SELECT):
        # callback_data contains index, not dir name (to avoid 64-byte limit)
        try:
            idx = int(data[len(CB_DIR_SELECT):])
        except ValueError:
            await query.answer("Invalid data")
            return

        # Look up dir name from cached subdirs
        cached_dirs: list[str] = context.user_data.get(BROWSE_DIRS_KEY, []) if context.user_data else []
        if idx < 0 or idx >= len(cached_dirs):
            await query.answer("Directory list changed, please refresh", show_alert=True)
            return
        subdir_name = cached_dirs[idx]

        default_path = str(Path.cwd())
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        new_path = (Path(current_path) / subdir_name).resolve()

        if not new_path.exists() or not new_path.is_dir():
            await query.answer("Directory not found", show_alert=True)
            return

        new_path_str = str(new_path)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = new_path_str
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard, subdirs = build_directory_browser(new_path_str)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_UP:
        default_path = str(Path.cwd())
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        current = Path(current_path).resolve()
        parent = current.parent
        # No restriction - allow navigating anywhere

        parent_path = str(parent)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = parent_path
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard, subdirs = build_directory_browser(parent_path)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data.startswith(CB_DIR_PAGE):
        try:
            pg = int(data[len(CB_DIR_PAGE):])
        except ValueError:
            await query.answer("Invalid data")
            return
        default_path = str(Path.cwd())
        current_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path
        if context.user_data is not None:
            context.user_data[BROWSE_PAGE_KEY] = pg

        msg_text, keyboard, subdirs = build_directory_browser(current_path, pg)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_CONFIRM:
        default_path = str(Path.cwd())
        selected_path = context.user_data.get(BROWSE_PATH_KEY, default_path) if context.user_data else default_path

        clear_browse_state(context.user_data)

        success, message, created_wname = await tmux_manager.create_window(selected_path)
        if success:
            session_manager.set_active_window(user.id, created_wname)

            # Wait for Claude Code's SessionStart hook to register in session_map
            await session_manager.wait_for_session_map_entry(created_wname)

            # Update the directory browser message to show refreshed session list
            active_items = await session_manager.list_active_sessions()
            list_text = f"📊 {len(active_items)} active sessions:"
            keyboard = await _build_list_keyboard(user.id)
            await safe_edit(query, list_text, reply_markup=keyboard)

            # Send creation success as a new message
            await safe_send(
                context.bot, user.id,
                f"✅ {message}\n\n_You can now send messages directly to this window._",
            )
        else:
            await safe_edit(query, f"❌ {message}")
        await query.answer("Created" if success else "Failed")

    elif data == CB_DIR_CANCEL:
        clear_browse_state(context.user_data)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Session action: History
    elif data.startswith(CB_SESSION_HISTORY):
        window_name = data[len(CB_SESSION_HISTORY):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await send_history(query.message, window_name)
        else:
            await safe_edit(query, "Window no longer exists.")
        await query.answer("Loading history")

    # Session action: Refresh
    elif data.startswith(CB_SESSION_REFRESH):
        window_name = data[len(CB_SESSION_REFRESH):]
        detail_text, action_buttons = await _build_session_detail(window_name)
        await safe_edit(query, detail_text, reply_markup=action_buttons)
        await query.answer("Refreshed")

    # Session action: Kill
    elif data.startswith(CB_SESSION_KILL):
        window_name = data[len(CB_SESSION_KILL):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.kill_window(w.window_id)
            # Clear active session if it was this one
            if user:
                active_wname = session_manager.get_active_window_name(user.id)
                if active_wname == window_name:
                    session_manager.set_active_window(user.id, "")
            await safe_edit(query, "🗑 Session killed.")
        else:
            await safe_edit(query, "Window already gone.")
        await query.answer("Killed")

    # Screenshot: Refresh
    elif data.startswith(CB_SCREENSHOT_REFRESH):
        window_name = data[len(CB_SCREENSHOT_REFRESH):]
        w = await tmux_manager.find_window_by_name(window_name)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            return

        text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if not text:
            await query.answer("Failed to capture pane", show_alert=True)
            return

        png_bytes = await text_to_image(text, with_ansi=True)
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
        w = await tmux_manager.find_window_by_name(wname) if wname else None
        if w:
            # Step 1: Clear active window to prevent message interleaving
            # During unread catch-up, we don't want new messages from either
            # old or new window to be sent (they would interleave with unread)
            session_manager.clear_active_session(user.id)

            # Step 2: Send UI feedback
            # Re-render list with checkmark on new window
            active_items = await session_manager.list_active_sessions()
            text = f"📊 {len(active_items)} active sessions:"
            keyboard = await _build_list_keyboard(user.id, pending_selection=w.window_name)
            await safe_edit(query, text, reply_markup=keyboard)

            # Send session detail message
            detail_text, action_buttons = await _build_session_detail(w.window_name)
            await safe_send(
                context.bot, user.id, detail_text,
                reply_markup=action_buttons,
            )

            # Step 3: Send unread catch-up (if any)
            unread_info = await session_manager.get_unread_info(user.id, w.window_name)
            if unread_info:
                if unread_info.has_unread:
                    # User has unread messages, send catch-up via send_history
                    await send_history(
                        None,  # target not used in direct send mode
                        w.window_name,
                        start_byte=unread_info.start_offset,
                        end_byte=unread_info.end_offset,
                        user_id=user.id,
                        bot=context.bot,
                    )
                else:
                    # First time or no unread - initialize offset to current file size
                    session_manager.update_user_window_offset(
                        user.id, w.window_name, unread_info.end_offset
                    )

            # Step 4: Now set active window (enables new message delivery)
            session_manager.set_active_window(user.id, w.window_name)

            await query.answer(f"Active: {w.window_name}")
        else:
            await query.answer("Window no longer exists", show_alert=True)

    # List: new session
    elif data == CB_LIST_NEW:
        # Start from current active window's cwd, fallback to browse_root_dir
        start_path = str(Path.cwd())
        active_wname = session_manager.get_active_window_name(user.id)
        if active_wname:
            w = await tmux_manager.find_window_by_name(active_wname)
            if w and w.cwd:
                start_path = w.cwd

        msg_text, keyboard, subdirs = build_directory_browser(start_path)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == "noop":
        await query.answer()

    # Interactive UI: Up arrow
    elif data.startswith(CB_ASK_UP):
        window_name = data[len(CB_ASK_UP):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Up", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer()

    # Interactive UI: Down arrow
    elif data.startswith(CB_ASK_DOWN):
        window_name = data[len(CB_ASK_DOWN):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Down", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer()

    # Interactive UI: Left arrow
    elif data.startswith(CB_ASK_LEFT):
        window_name = data[len(CB_ASK_LEFT):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Left", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer()

    # Interactive UI: Right arrow
    elif data.startswith(CB_ASK_RIGHT):
        window_name = data[len(CB_ASK_RIGHT):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Right", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer()

    # Interactive UI: Escape
    elif data.startswith(CB_ASK_ESC):
        window_name = data[len(CB_ASK_ESC):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Escape", enter=False, literal=False)
            await clear_interactive_msg(user.id, context.bot)
        await query.answer("⎋ Esc")

    # Interactive UI: Enter
    elif data.startswith(CB_ASK_ENTER):
        window_name = data[len(CB_ASK_ENTER):]
        w = await tmux_manager.find_window_by_name(window_name)
        if w:
            await tmux_manager.send_keys(w.window_id, "Enter", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer("⏎ Enter")

    # Interactive UI: refresh display
    elif data.startswith(CB_ASK_REFRESH):
        window_name = data[len(CB_ASK_REFRESH):]
        await handle_interactive_ui(context.bot, user.id, window_name)
        await query.answer("🔄")


# --- Streaming response / notifications ---


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
            set_interactive_mode(user_id, wname)
            # Flush pending messages (e.g. plan content) before sending interactive UI
            queue = get_message_queue(user_id)
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
                from .handlers.interactive_ui import clear_interactive_mode
                clear_interactive_mode(user_id)

        # Any non-interactive message means the interaction is complete — delete the UI message
        if get_interactive_msg_id(user_id):
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
    _status_poll_task = asyncio.create_task(status_poll_loop(application.bot))
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
    await shutdown_workers()

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")


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
