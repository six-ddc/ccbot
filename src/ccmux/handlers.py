"""Command and callback handlers for the CCMux Telegram bot.

Extracted handler functions for bot commands and user interactions.
Core responsibilities:
  - Command handlers: start_command, text_handler, forward_command_handler,
    list_command, history_command, screenshot_command, esc_command
  - Callback handler: handles all inline keyboard interactions including
    session selection, directory browser, history pagination, and interactive UI
  - Helper functions: is_user_allowed, _clear_browse_state, and UI builders

Key functions: start_command(), callback_handler(), text_handler(),
forward_command_handler(), list_command(), history_command(),
screenshot_command(), esc_command().
"""

import asyncio
import io
import logging
from pathlib import Path

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .config import config
from .screenshot import text_to_image
from .session import session_manager
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

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

# Interactive UI callback prefixes (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"       # aq:up:<window>
CB_ASK_DOWN = "aq:down:"   # aq:down:<window>
CB_ASK_LEFT = "aq:left:"   # aq:left:<window>
CB_ASK_RIGHT = "aq:right:" # aq:right:<window>
CB_ASK_ESC = "aq:esc:"     # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:" # aq:enter:<window>
CB_ASK_REFRESH = "aq:ref:" # aq:ref:<window>

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
BROWSE_DIRS_KEY = "browse_dirs"  # Cache of subdirs for current path

# Claude Code commands shown in bot menu (forwarded via tmux)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
    "memory": "↗ Edit CLAUDE.md",
}


def is_user_allowed(user_id: int | None) -> bool:
    """Check if user is authorized to use the bot."""
    return user_id is not None and config.is_user_allowed(user_id)


def _clear_browse_state(user_data: dict | None) -> None:
    """Clear directory browsing state keys from user_data."""
    if user_data is not None:
        user_data.pop(STATE_KEY, None)
        user_data.pop(BROWSE_PATH_KEY, None)
        user_data.pop(BROWSE_PAGE_KEY, None)
        user_data.pop(BROWSE_DIRS_KEY, None)


# --- Command / message handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    from .message_sender import safe_reply
    
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    _clear_browse_state(context.user_data)

    if update.message:
        # Remove any existing reply keyboard
        await safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Use /list to see sessions.\n"
            "Send text to forward to the active session.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages from user."""
    from .interactive_ui import get_interactive_window, handle_interactive_ui
    from .message_queue import clear_status_tracking
    from .message_sender import safe_reply
    
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
        clear_status_tracking(user.id)

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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all callback queries from inline keyboards."""
    from .history import send_history
    from .interactive_ui import clear_interactive_msg, get_interactive_window, handle_interactive_ui
    from .message_sender import safe_edit, safe_send
    from .ui_components import build_directory_browser, build_session_detail

    
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

        _clear_browse_state(context.user_data)

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
        _clear_browse_state(context.user_data)
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
        detail_text, action_buttons = await build_session_detail(window_name)
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
            detail_text, action_buttons = await build_session_detail(w.window_name)
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


async def forward_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    from .message_sender import safe_reply
    
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


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active sessions as inline buttons."""
    from .message_sender import safe_reply
    
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
    from .history import send_history
    from .message_sender import safe_reply
    
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
    from .message_sender import safe_reply
    
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
    from .message_sender import safe_reply
    
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
