"""Telegram bot handlers — the main UI layer of CCBot.

Registers all command/callback/message handlers and manages the bot lifecycle.
Each Telegram topic maps 1:1 to a tmux window (Claude session).

Core responsibilities:
  - Command handlers: /new (+ /start alias), /history, /sessions,
    plus forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: thin dispatcher routing to dedicated handler modules.
  - Topic-based routing: each named topic binds to one tmux window.
    Unbound topics trigger the directory browser to create a new session.
  - Automatic cleanup: closing a topic kills the associated window
    (topic_closed_handler). Unsupported content (images, stickers, etc.)
    is rejected with a warning (unsupported_content_handler).
  - Bot lifecycle management: post_init, post_shutdown, create_bot.

Key functions: create_bot(), handle_new_message().
"""

import asyncio
import contextlib
import logging
from pathlib import Path

from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .cc_commands import register_commands
from .config import config
from .handlers.callback_data import (
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_RESUME,
    CB_SCREENSHOT_REFRESH,
    CB_SESSIONS_KILL,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
    CB_STATUS_ESC,
    CB_STATUS_SCREENSHOT,
    CB_WIN_BIND,
    CB_WIN_CANCEL,
    CB_WIN_NEW,
)
from .handlers.callback_helpers import get_thread_id as _get_thread_id
from .handlers.callback_helpers import user_owns_window as _user_owns_window
from .handlers.directory_callbacks import handle_directory_callback
from .handlers.history_callbacks import handle_history_callback
from .handlers.interactive_callbacks import (
    handle_interactive_callback,
    match_interactive_prefix as _match_interactive_prefix,
)
from .handlers.recovery_callbacks import (
    build_recovery_keyboard as _build_recovery_keyboard,
    handle_recovery_callback,
)
from .handlers.screenshot_callbacks import handle_screenshot_callback
from .handlers.window_callbacks import handle_window_callback
from .handlers.directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_window_picker,
    clear_browse_state,
    clear_window_picker_state,
)
from .handlers.cleanup import clear_topic_state
from .handlers.history import send_history
from .handlers.sessions_dashboard import (
    handle_sessions_kill,
    handle_sessions_kill_confirm,
    handle_sessions_refresh,
    sessions_command,
)
from .handlers.interactive_ui import (
    INTERACTIVE_TOOL_NAMES,
    clear_interactive_mode,
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
    shutdown_workers,
)
from .handlers.message_sender import (
    NO_LINK_PREVIEW,
    rate_limit_send_message,
    safe_reply,
)
from .markdown_v2 import convert_markdown
from .handlers.response_builder import build_response_parts
from .handlers.status_polling import status_poll_loop
from .session import session_manager
from .session_monitor import NewMessage, NewWindowEvent, SessionMonitor
from .terminal_parser import extract_bash_output
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Maximum characters for bash output before truncation (fits Telegram 4096-char limit)
_BASH_OUTPUT_LIMIT = 3800

# Session monitor instance
session_monitor: SessionMonitor | None = None

# Status polling task
_status_poll_task: asyncio.Task | None = None


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


# Group filter: when CCBOT_GROUP_ID is set, only process updates from that group.
# filters.ALL is a no-op — single-instance backward compat.
_group_filter: filters.BaseFilter = (
    filters.Chat(chat_id=config.group_id) if config.group_id else filters.ALL
)


# --- Command handlers ---


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    clear_browse_state(context.user_data)

    if update.message:
        await safe_reply(
            update.message,
            "\U0001f916 *Claude Code Monitor*\n\n"
            "Each topic is a session. Create a new topic to start.",
        )


async def history_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session or bound thread."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "\u274c No session bound to this topic.")
        return

    await send_history(update.message, wid)


async def topic_closed_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic closure — kill the associated tmux window and clean up state."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if wid:
        display = session_manager.get_display_name(wid)
        w = await tmux_manager.find_window_by_id(wid)
        if w:
            await tmux_manager.kill_window(w.window_id)
            logger.info(
                "Topic closed: killed window %s (user=%d, thread=%d)",
                display,
                user.id,
                thread_id,
            )
        else:
            logger.info(
                "Topic closed: window %s already gone (user=%d, thread=%d)",
                display,
                user.id,
                thread_id,
            )
        session_manager.unbind_thread(user.id, thread_id)
        # Clean up all memory state for this topic
        await clear_topic_state(user.id, thread_id, context.bot, context.user_data)
    else:
        logger.debug(
            "Topic closed: no binding (user=%d, thread=%d)", user.id, thread_id
        )


async def forward_command_handler(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    # Store group chat_id for forum topic message routing
    chat = update.message.chat
    thread_id = _get_thread_id(update)
    if chat.type in ("group", "supergroup") and thread_id is not None:
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    cmd_text = update.message.text or ""
    # The full text is already a slash command like "/clear" or "/compact foo"
    cc_slash = cmd_text.split("@")[0]  # strip bot mention
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "\u274c No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"\u274c Window '{display}' no longer exists.")
        return

    display = session_manager.get_display_name(wid)
    logger.info(
        "Forwarding command %s to window %s (user=%d)", cc_slash, display, user.id
    )
    await update.message.chat.send_action(ChatAction.TYPING)
    success, message = await session_manager.send_to_window(wid, cc_slash)
    if success:
        await safe_reply(update.message, f"\u26a1 [{display}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            logger.info("Clearing session for window %s after /clear", display)
            session_manager.clear_window_session(wid)
    else:
        await safe_reply(update.message, f"\u274c {message}")


async def unsupported_content_handler(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reply to non-text messages (images, stickers, voice, etc.)."""
    if not update.message:
        return
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    logger.debug("Unsupported content from user %d", user.id)
    await safe_reply(
        update.message,
        "\u26a0 Only text messages are supported. Images, stickers, voice, and other media cannot be forwarded to Claude Code.",
    )


# Active bash capture tasks: (user_id, thread_id) -> asyncio.Task
_bash_capture_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}


def _cancel_bash_capture(user_id: int, thread_id: int) -> None:
    """Cancel any running bash capture for this topic."""
    key = (user_id, thread_id)
    task = _bash_capture_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


async def _capture_bash_output(
    bot: Bot,
    user_id: int,
    thread_id: int,
    window_id: str,
    command: str,
) -> None:
    """Background task: capture ``!`` bash command output from tmux pane.

    Sends the first captured output as a new message, then edits it
    in-place as more output appears.  Stops after 30 s or when cancelled
    (e.g. user sends a new message, which pushes content down).
    """
    try:
        # Wait for the command to start producing output
        await asyncio.sleep(2.0)

        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        msg_id: int | None = None
        last_output: str = ""

        for _ in range(30):
            raw = await tmux_manager.capture_pane(window_id)
            if raw is None:
                return

            output = extract_bash_output(raw, command)
            if not output:
                await asyncio.sleep(1.0)
                continue

            # Skip edit if nothing changed
            if output == last_output:
                await asyncio.sleep(1.0)
                continue

            last_output = output

            # Truncate to fit Telegram's 4096-char limit
            if len(output) > _BASH_OUTPUT_LIMIT:
                output = "\u2026 " + output[-_BASH_OUTPUT_LIMIT:]

            if msg_id is None:
                # First capture — send a new message
                sent = await rate_limit_send_message(
                    bot,
                    chat_id,
                    output,
                    message_thread_id=thread_id,
                )
                if sent:
                    msg_id = sent.message_id
            else:
                # Subsequent captures — edit in place
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=convert_markdown(output),
                        parse_mode="MarkdownV2",
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                except TelegramError:
                    with contextlib.suppress(TelegramError):
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=output,
                            link_preview_options=NO_LINK_PREVIEW,
                        )

            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return
    finally:
        _bash_capture_tasks.pop((user_id, thread_id), None)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    # Store group chat_id for forum topic message routing
    chat = update.message.chat
    thread_id = _get_thread_id(update)
    if chat.type in ("group", "supergroup") and thread_id is not None:
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    text = update.message.text

    # Ignore text in window picker mode (only for the same thread)
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_SELECTING_WINDOW:
        pending_tid = context.user_data.get("_pending_thread_id")
        if pending_tid == thread_id:
            await safe_reply(
                update.message,
                "Please use the window picker above, or tap Cancel.",
            )
            return
        # Stale picker state from a different thread — clear it
        clear_window_picker_state(context.user_data)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)

    # Ignore text in directory browsing mode (only for the same thread)
    if (
        context.user_data
        and context.user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY
    ):
        pending_tid = context.user_data.get("_pending_thread_id")
        if pending_tid == thread_id:
            await safe_reply(
                update.message,
                "Please use the directory browser above, or tap Cancel.",
            )
            return
        # Stale browsing state from a different thread — clear it
        clear_browse_state(context.user_data)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)

    # Must be in a named topic
    if thread_id is None:
        await safe_reply(
            update.message,
            "\u274c Please use a named topic. Create a new topic to start a session.",
        )
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if wid is None:
        # Unbound topic — check for unbound windows first
        all_windows = await tmux_manager.list_windows()
        bound_ids = {wid for _, _, wid in session_manager.iter_thread_bindings()}
        unbound = [
            (w.window_id, w.window_name, w.cwd)
            for w in all_windows
            if w.window_id not in bound_ids
        ]
        logger.debug(
            "Window picker check: all=%s, bound=%s, unbound=%s",
            [w.window_name for w in all_windows],
            bound_ids,
            [name for _, name, _ in unbound],
        )

        if unbound:
            # Show window picker
            logger.info(
                "Unbound topic: showing window picker (%d unbound windows, user=%d, thread=%d)",
                len(unbound),
                user.id,
                thread_id,
            )
            msg_text, keyboard, win_ids = build_window_picker(unbound)
            if context.user_data is not None:
                context.user_data[STATE_KEY] = STATE_SELECTING_WINDOW
                context.user_data[UNBOUND_WINDOWS_KEY] = win_ids
                context.user_data["_pending_thread_id"] = thread_id
                context.user_data["_pending_thread_text"] = text
            await safe_reply(update.message, msg_text, reply_markup=keyboard)
            return

        # No unbound windows — show directory browser to create a new session
        logger.info(
            "Unbound topic: showing directory browser (user=%d, thread=%d)",
            user.id,
            thread_id,
        )
        start_path = str(Path.cwd())
        msg_text, keyboard, subdirs = build_directory_browser(start_path)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
            context.user_data[BROWSE_DIRS_KEY] = subdirs
            context.user_data["_pending_thread_id"] = thread_id
            context.user_data["_pending_thread_text"] = text
        await safe_reply(update.message, msg_text, reply_markup=keyboard)
        return

    # Bound topic — forward to bound window
    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        ws = session_manager.get_window_state(wid)
        cwd = ws.cwd if ws.cwd else ""

        if not cwd or not Path(cwd).is_dir():
            # No valid cwd — unbind and fall back to directory browser
            logger.info(
                "Dead window %s (no valid cwd), falling back to directory browser"
                " (user=%d, thread=%d)",
                wid,
                user.id,
                thread_id,
            )
            session_manager.unbind_thread(user.id, thread_id)
            start_path = str(Path.cwd())
            msg_text, keyboard, subdirs = build_directory_browser(start_path)
            if context.user_data is not None:
                context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
                context.user_data[BROWSE_PATH_KEY] = start_path
                context.user_data[BROWSE_PAGE_KEY] = 0
                context.user_data[BROWSE_DIRS_KEY] = subdirs
                context.user_data["_pending_thread_id"] = thread_id
                context.user_data["_pending_thread_text"] = text
            await safe_reply(update.message, msg_text, reply_markup=keyboard)
            return

        # Show recovery UI
        logger.info(
            "Dead window %s (%s), showing recovery UI (user=%d, thread=%d)",
            wid,
            display,
            user.id,
            thread_id,
        )
        if context.user_data is not None:
            context.user_data["_pending_thread_id"] = thread_id
            context.user_data["_pending_thread_text"] = text
            context.user_data["_recovery_window_id"] = wid
        keyboard = _build_recovery_keyboard(wid)
        await safe_reply(
            update.message,
            f"\u26a0 Window `{display}` is no longer running.\n"
            f"\U0001f4c2 `{cwd}`\n\n"
            "How would you like to recover?",
            reply_markup=keyboard,
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    clear_status_msg_info(user.id, thread_id)

    # Cancel any running bash capture — new message pushes pane content down
    _cancel_bash_capture(user.id, thread_id)

    success, message = await session_manager.send_to_window(wid, text)
    if not success:
        await safe_reply(update.message, f"\u274c {message}")
        return

    # Start background capture for ! bash command output
    if text.startswith("!") and len(text) > 1:
        bash_cmd = text[1:]  # strip leading "!"
        task = asyncio.create_task(
            _capture_bash_output(context.bot, user.id, thread_id, wid, bash_cmd)
        )
        _bash_capture_tasks[(user.id, thread_id)] = task

    # If in interactive mode, refresh the UI after sending text
    interactive_window = get_interactive_window(user.id, thread_id)
    if interactive_window and interactive_window == wid:
        await asyncio.sleep(0.2)
        await handle_interactive_ui(context.bot, user.id, wid, thread_id)


# --- Callback query handler (thin dispatcher) ---

# Callback prefixes that route to dedicated handler modules.
# Order matters: prefixes checked via startswith must be longest-first
# to avoid false matches (e.g. CB_SESSIONS_KILL_CONFIRM before CB_SESSIONS_KILL).
_CB_HISTORY = (CB_HISTORY_PREV, CB_HISTORY_NEXT)
_CB_DIRECTORY = (CB_DIR_SELECT, CB_DIR_UP, CB_DIR_PAGE, CB_DIR_CONFIRM, CB_DIR_CANCEL)
_CB_WINDOW = (CB_WIN_BIND, CB_WIN_NEW, CB_WIN_CANCEL)
_CB_SCREENSHOT = (
    CB_SCREENSHOT_REFRESH,
    CB_STATUS_ESC,
    CB_STATUS_SCREENSHOT,
    CB_KEYS_PREFIX,
)
_CB_RECOVERY = (
    CB_RECOVERY_FRESH,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_RESUME,
    CB_RECOVERY_CANCEL,
)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch callback queries to dedicated handler modules."""
    # CallbackQueryHandler doesn't support filters= param, so check inline.
    if config.group_id:
        chat = update.effective_chat
        if not chat or chat.id != config.group_id:
            return

    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        await query.answer("Not authorized")
        return

    # Store group chat_id for forum topic message routing
    if query.message and query.message.chat.type in ("group", "supergroup"):
        cb_thread_id = _get_thread_id(update)
        if cb_thread_id is not None:
            session_manager.set_group_chat_id(
                user.id, cb_thread_id, query.message.chat.id
            )

    data = query.data

    # History pagination
    if data.startswith(_CB_HISTORY):
        await handle_history_callback(query, user.id, data, update, context)

    # Directory browser
    elif data.startswith(_CB_DIRECTORY):
        await handle_directory_callback(query, user.id, data, update, context)

    # Window picker
    elif data.startswith(_CB_WINDOW):
        await handle_window_callback(query, user.id, data, update, context)

    # Screenshot / status buttons / quick keys
    elif data.startswith(_CB_SCREENSHOT):
        await handle_screenshot_callback(query, user.id, data, update, context)

    # No-op
    elif data == "noop":
        await query.answer()

    # Interactive UI (AskUserQuestion / ExitPlanMode navigation)
    elif _match_interactive_prefix(data):
        await handle_interactive_callback(query, user.id, data, update, context)

    # Recovery UI
    elif data.startswith(_CB_RECOVERY):
        await handle_recovery_callback(query, user.id, data, update, context)

    # Sessions dashboard
    elif data == CB_SESSIONS_REFRESH:
        await handle_sessions_refresh(query, user.id)
        await query.answer("Refreshed")
    elif data == CB_SESSIONS_NEW:
        await query.answer("Create a new topic to start a session.")
    elif data.startswith(CB_SESSIONS_KILL_CONFIRM):
        window_id = data[len(CB_SESSIONS_KILL_CONFIRM) :]
        if not _user_owns_window(user.id, window_id):
            await query.answer("Not your session", show_alert=True)
            return
        await handle_sessions_kill_confirm(query, user.id, window_id, context.bot)
        await query.answer("Killed")
    elif data.startswith(CB_SESSIONS_KILL):
        window_id = data[len(CB_SESSIONS_KILL) :]
        if not _user_owns_window(user.id, window_id):
            await query.answer("Not your session", show_alert=True)
            return
        await handle_sessions_kill(query, user.id, window_id)
        await query.answer()


# --- Streaming response / notifications ---


async def handle_new_message(msg: NewMessage, bot: Bot) -> None:
    """Handle a new assistant message — enqueue for sequential processing.

    Messages are queued per-user to ensure status messages always appear last.
    Routes via thread_bindings to deliver to the correct topic.
    """
    status = "complete" if msg.is_complete else "streaming"
    logger.info(
        "handle_new_message [%s]: session=%s, text_len=%d",
        status,
        msg.session_id,
        len(msg.text),
    )

    # Find users whose thread-bound window matches this session
    active_users = session_manager.find_users_for_session(msg.session_id)

    if not active_users:
        logger.info("No active users for session %s", msg.session_id)
        return

    for user_id, wid, thread_id in active_users:
        # Handle interactive tools specially - capture terminal and send UI
        if msg.tool_name in INTERACTIVE_TOOL_NAMES and msg.content_type == "tool_use":
            # Mark interactive mode BEFORE sleeping so polling skips this window
            set_interactive_mode(user_id, wid, thread_id)
            # Flush pending messages (e.g. plan content) before sending interactive UI
            queue = get_message_queue(user_id)
            if queue:
                await queue.join()
            # Wait briefly for Claude Code to render the question UI
            await asyncio.sleep(0.3)
            handled = await handle_interactive_ui(bot, user_id, wid, thread_id)
            if handled:
                # Update user's read offset
                session = await session_manager.resolve_session_for_window(wid)
                if session and session.file_path:
                    try:
                        file_size = Path(session.file_path).stat().st_size
                        session_manager.update_user_window_offset(
                            user_id, wid, file_size
                        )
                    except OSError:
                        pass
                continue  # Don't send the normal tool_use message
            else:
                # UI not rendered — clear the early-set mode
                clear_interactive_mode(user_id, thread_id)

        # Any non-interactive message means the interaction is complete — delete the UI message
        if get_interactive_msg_id(user_id, thread_id):
            await clear_interactive_msg(user_id, bot, thread_id)

        parts = build_response_parts(
            msg.text,
            msg.is_complete,
            msg.content_type,
            msg.role,
        )

        if msg.is_complete:
            # Enqueue content message task
            # Note: tool_result editing is handled inside _process_content_task
            # to ensure sequential processing with tool_use message sending
            await enqueue_content_message(
                bot=bot,
                user_id=user_id,
                window_id=wid,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
                text=msg.text,
                thread_id=thread_id,
            )

            # Update user's read offset to current file position
            # This marks these messages as "read" for this user
            session = await session_manager.resolve_session_for_window(wid)
            if session and session.file_path:
                try:
                    file_size = Path(session.file_path).stat().st_size
                    session_manager.update_user_window_offset(user_id, wid, file_size)
                except OSError:
                    pass


# --- Auto-create topic for new tmux windows ---


async def _handle_new_window(event: NewWindowEvent, bot: Bot) -> None:
    """Create a Telegram forum topic for a newly detected tmux window.

    Skips if the window is already bound to a topic. Creates one topic per
    unique group chat, binds all users in that chat.
    """

    # Check if this window is already bound to any topic
    for _, _, bound_wid in session_manager.iter_thread_bindings():
        if bound_wid == event.window_id:
            logger.debug(
                "New window %s already bound, skipping topic creation", event.window_id
            )
            return

    topic_name = event.window_name or Path(event.cwd).name or event.window_id

    # Collect unique chat_ids from existing bindings
    seen_chats: set[int] = set()
    for uid, tid, _ in session_manager.iter_thread_bindings():
        chat_id = session_manager.resolve_chat_id(uid, tid)
        if chat_id != uid:  # Only group chats (not fallback to user_id)
            seen_chats.add(chat_id)

    if not seen_chats:
        logger.debug(
            "No group chats found for auto-topic creation (window %s)", event.window_id
        )
        return

    for chat_id in seen_chats:
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
            logger.info(
                "Auto-created topic '%s' (thread=%d) in chat %d for window %s",
                topic_name,
                topic.message_thread_id,
                chat_id,
                event.window_id,
            )
            # Bind all users that have bindings in this chat
            for uid, tid, _ in session_manager.iter_thread_bindings():
                if session_manager.resolve_chat_id(uid, tid) == chat_id:
                    session_manager.bind_thread(
                        uid,
                        topic.message_thread_id,
                        event.window_id,
                        window_name=topic_name,
                    )
                    session_manager.set_group_chat_id(
                        uid, topic.message_thread_id, chat_id
                    )
                    break  # One binding per chat is enough to establish the route
        except TelegramError as e:
            logger.error(
                "Failed to create topic for window %s in chat %d: %s",
                event.window_id,
                chat_id,
                e,
            )


# --- App lifecycle ---


async def post_init(application: Application) -> None:
    global session_monitor, _status_poll_task

    await register_commands(application.bot)

    # Refresh CC commands every 10 minutes (picks up new skills/commands)
    async def _refresh_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
        if context.bot:
            try:
                await register_commands(context.bot)
            except Exception:
                logger.exception("Failed to refresh CC commands, keeping previous menu")

    if application.job_queue:
        application.job_queue.run_repeating(_refresh_commands, interval=600, first=600)

    # Re-resolve stale window IDs from persisted state against live tmux windows
    await session_manager.resolve_stale_ids()

    monitor = SessionMonitor()

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot)

    monitor.set_message_callback(message_callback)

    async def new_window_callback(event: NewWindowEvent) -> None:
        await _handle_new_window(event, application.bot)

    monitor.set_new_window_callback(new_window_callback)
    monitor.start()
    session_monitor = monitor
    logger.info("Session monitor started")

    # Start status polling task (routed through PTB error handler)
    _status_poll_task = application.create_task(status_poll_loop(application.bot))
    logger.info("Status polling task started")


async def post_shutdown(_application: Application) -> None:
    global _status_poll_task

    # Stop status polling
    if _status_poll_task:
        _status_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _status_poll_task
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

    application.add_handler(CommandHandler("new", new_command, filters=_group_filter))
    application.add_handler(
        CommandHandler("start", new_command, filters=_group_filter)  # compat alias
    )
    application.add_handler(
        CommandHandler("history", history_command, filters=_group_filter)
    )
    application.add_handler(
        CommandHandler("sessions", sessions_command, filters=_group_filter)
    )
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Topic closed event — auto-kill associated window
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CLOSED & _group_filter,
            topic_closed_handler,
        )
    )
    # Forward any other /command to Claude Code
    application.add_handler(
        MessageHandler(filters.COMMAND & _group_filter, forward_command_handler)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & _group_filter, text_handler)
    )
    # Catch-all: non-text content (images, stickers, voice, etc.)
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND
            & ~filters.TEXT
            & ~filters.StatusUpdate.ALL
            & _group_filter,
            unsupported_content_handler,
        )
    )

    return application
