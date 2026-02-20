"""Telegram bot handlers — the main UI layer of CCBot.

Registers all command/callback/message handlers and manages the bot lifecycle.
Each Telegram topic maps 1:1 to a tmux window (Claude session).

Core responsibilities:
  - Command handlers: /start, /history, /screenshot, /esc, /kill, /unbind,
    plus forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: directory browser, history pagination,
    interactive UI navigation, screenshot refresh.
  - Topic-based routing: each named topic binds to one tmux window.
    Unbound topics trigger the directory browser to create a new session.
  - Photo handling: photos sent by user are downloaded and forwarded
    to Claude Code as file paths (photo_handler).
  - Automatic cleanup: closing a topic kills the associated window
    (topic_closed_handler). Unsupported content (stickers, voice, etc.)
    is rejected with a warning (unsupported_content_handler).
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
import time
from pathlib import Path

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    AIORateLimiter,
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
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    CB_WIN_BIND,
    CB_WIN_CANCEL,
    CB_WIN_NEW,
)
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
    enqueue_status_update,
    get_message_queue,
    shutdown_workers,
)
from .handlers.message_sender import (
    NO_LINK_PREVIEW,
    safe_edit,
    safe_reply,
    safe_send,
    send_with_fallback,
)
from .markdown_v2 import convert_markdown
from .handlers.response_builder import build_response_parts
from .handlers.status_polling import status_poll_loop
from .screenshot import text_to_image
from .session import session_manager
from .session_monitor import NewMessage, SessionMonitor
from .terminal_parser import extract_bash_output
from .tmux_manager import tmux_manager
from .utils import ccbot_dir

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
    "model": "↗ Switch AI model",
}


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


def _get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic."""
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid


# --- Command handlers ---


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    clear_browse_state(context.user_data)

    if update.message:
        await safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Each topic is a session. Create a new topic to start.",
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session or bound thread."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    await send_history(update.message, wid)


async def screenshot_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture the current tmux pane and send it as an image."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = _build_screenshot_keyboard(wid)
    await update.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=keyboard,
    )


async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unbind this topic from its Claude session without killing the window."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "❌ This command only works in a topic.")
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    display = session_manager.get_display_name(wid)
    session_manager.unbind_thread(user.id, thread_id)
    await clear_topic_state(user.id, thread_id, context.bot, context.user_data)

    await safe_reply(
        update.message,
        f"✅ Topic unbound from window '{display}'.\n"
        "The Claude session is still running in tmux.\n"
        "Send a message to bind to a new session.",
    )


async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Escape key to interrupt Claude."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    # Send Escape control character (no enter)
    await tmux_manager.send_keys(w.window_id, "\x1b", enter=False)
    await safe_reply(update.message, "⎋ Sent Escape")


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch Claude Code usage stats from TUI and send to Telegram."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        await safe_reply(update.message, f"Window '{wid}' no longer exists.")
        return

    # Send /usage command to Claude Code TUI
    await tmux_manager.send_keys(w.window_id, "/usage")
    # Wait for the modal to render
    await asyncio.sleep(2.0)
    # Capture the pane content
    pane_text = await tmux_manager.capture_pane(w.window_id)
    # Dismiss the modal
    await tmux_manager.send_keys(w.window_id, "Escape", enter=False, literal=False)

    if not pane_text:
        await safe_reply(update.message, "Failed to capture usage info.")
        return

    # Try to parse structured usage info
    from .terminal_parser import parse_usage_output

    usage = parse_usage_output(pane_text)
    if usage and usage.parsed_lines:
        text = "\n".join(usage.parsed_lines)
        await safe_reply(update.message, f"```\n{text}\n```")
    else:
        # Fallback: send raw pane capture trimmed
        trimmed = pane_text.strip()
        if len(trimmed) > 3000:
            trimmed = trimmed[:3000] + "\n... (truncated)"
        await safe_reply(update.message, f"```\n{trimmed}\n```")


# --- Screenshot keyboard with quick control keys ---

# key_id → (tmux_key, enter, literal)
_KEYS_SEND_MAP: dict[str, tuple[str, bool, bool]] = {
    "up": ("Up", False, False),
    "dn": ("Down", False, False),
    "lt": ("Left", False, False),
    "rt": ("Right", False, False),
    "esc": ("Escape", False, False),
    "ent": ("Enter", False, False),
    "spc": ("Space", False, False),
    "tab": ("Tab", False, False),
    "cc": ("C-c", False, False),
}

# key_id → display label (shown in callback answer toast)
_KEY_LABELS: dict[str, str] = {
    "up": "↑",
    "dn": "↓",
    "lt": "←",
    "rt": "→",
    "esc": "⎋ Esc",
    "ent": "⏎ Enter",
    "spc": "␣ Space",
    "tab": "⇥ Tab",
    "cc": "^C",
}


def _build_screenshot_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for screenshot: control keys + refresh."""

    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{window_id}"[:64],
        )

    return InlineKeyboardMarkup(
        [
            [btn("␣ Space", "spc"), btn("↑", "up"), btn("⇥ Tab", "tab")],
            [btn("←", "lt"), btn("↓", "dn"), btn("→", "rt")],
            [btn("⎋ Esc", "esc"), btn("^C", "cc"), btn("⏎ Enter", "ent")],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{window_id}"[:64],
                )
            ],
        ]
    )


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
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove — see session.py docs.
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    cmd_text = update.message.text or ""
    # The full text is already a slash command like "/clear" or "/compact foo"
    cc_slash = cmd_text.split("@")[0]  # strip bot mention
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    display = session_manager.get_display_name(wid)
    logger.info(
        "Forwarding command %s to window %s (user=%d)", cc_slash, display, user.id
    )
    await update.message.chat.send_action(ChatAction.TYPING)
    success, message = await session_manager.send_to_window(wid, cc_slash)
    if success:
        await safe_reply(update.message, f"⚡ [{display}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            logger.info("Clearing session for window %s after /clear", display)
            session_manager.clear_window_session(wid)

        # Interactive commands (e.g. /model) render a terminal-based UI
        # with no JSONL tool_use entry.  The status poller already detects
        # interactive UIs every 1s (status_polling.py), so no
        # proactive detection needed here — the poller handles it.
    else:
        await safe_reply(update.message, f"❌ {message}")


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
        "⚠ Only text messages are supported. Images, stickers, voice, and other media cannot be forwarded to Claude Code.",
    )


# --- Image directory for incoming photos ---
_IMAGES_DIR = ccbot_dir() / "images"
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos sent by the user: download and forward path to Claude Code."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.photo:
        return

    chat = update.message.chat
    thread_id = _get_thread_id(update)
    if chat.type in ("group", "supergroup") and thread_id is not None:
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    # Must be in a named topic
    if thread_id is None:
        await safe_reply(
            update.message,
            "❌ Please use a named topic. Create a new topic to start a session.",
        )
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if wid is None:
        await safe_reply(
            update.message,
            "❌ No session bound to this topic. Send a text message first to create one.",
        )
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        session_manager.unbind_thread(user.id, thread_id)
        await safe_reply(
            update.message,
            f"❌ Window '{display}' no longer exists. Binding removed.\n"
            "Send a message to start a new session.",
        )
        return

    # Download the highest-resolution photo
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()

    # Save to ~/.ccbot/images/<timestamp>_<file_unique_id>.jpg
    filename = f"{int(time.time())}_{photo.file_unique_id}.jpg"
    file_path = _IMAGES_DIR / filename
    await tg_file.download_to_drive(file_path)

    # Build the message to send to Claude Code
    caption = update.message.caption or ""
    if caption:
        text_to_send = f"{caption}\n\n(image attached: {file_path})"
    else:
        text_to_send = f"(image attached: {file_path})"

    await update.message.chat.send_action(ChatAction.TYPING)
    clear_status_msg_info(user.id, thread_id)

    success, message = await session_manager.send_to_window(wid, text_to_send)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    # Confirm to user
    await safe_reply(update.message, "📷 Image sent to Claude Code.")


# Active bash capture tasks: (user_id, thread_id) → asyncio.Task
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
            if len(output) > 3800:
                output = "… " + output[-3800:]

            if msg_id is None:
                # First capture — send a new message
                sent = await send_with_fallback(
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
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=output,
                            link_preview_options=NO_LINK_PREVIEW,
                        )
                    except Exception:
                        pass

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

    thread_id = _get_thread_id(update)

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove — see session.py docs.
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
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
            "❌ Please use a named topic. Create a new topic to start a session.",
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
        logger.info(
            "Stale binding: window %s gone, unbinding (user=%d, thread=%d)",
            display,
            user.id,
            thread_id,
        )
        session_manager.unbind_thread(user.id, thread_id)
        await safe_reply(
            update.message,
            f"❌ Window '{display}' no longer exists. Binding removed.\n"
            "Send a message to start a new session.",
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    await enqueue_status_update(context.bot, user.id, wid, None, thread_id=thread_id)

    # Cancel any running bash capture — new message pushes pane content down
    _cancel_bash_capture(user.id, thread_id)

    success, message = await session_manager.send_to_window(wid, text)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
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

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove — see session.py docs.
    cb_thread_id = _get_thread_id(update)
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, cb_thread_id, chat.id)

    # History: older/newer pagination
    # Format: hp:<page>:<window_id>:<start>:<end> or hn:<page>:<window_id>:<start>:<end>
    if data.startswith(CB_HISTORY_PREV) or data.startswith(CB_HISTORY_NEXT):
        prefix_len = len(CB_HISTORY_PREV)  # same length for both
        rest = data[prefix_len:]
        try:
            parts = rest.split(":")
            if len(parts) < 4:
                # Old format without byte range: page:window_id
                offset_str, window_id = rest.split(":", 1)
                start_byte, end_byte = 0, 0
            else:
                # New format: page:window_id:start:end (window_id may contain colons)
                offset_str = parts[0]
                start_byte = int(parts[-2])
                end_byte = int(parts[-1])
                window_id = ":".join(parts[1:-2])
            offset = int(offset_str)
        except (ValueError, IndexError):
            await query.answer("Invalid data")
            return

        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await send_history(
                query,
                window_id,
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
        # Validate: callback must come from the same topic that started browsing
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        # callback_data contains index, not dir name (to avoid 64-byte limit)
        try:
            idx = int(data[len(CB_DIR_SELECT) :])
        except ValueError:
            await query.answer("Invalid data")
            return

        # Look up dir name from cached subdirs
        cached_dirs: list[str] = (
            context.user_data.get(BROWSE_DIRS_KEY, []) if context.user_data else []
        )
        if idx < 0 or idx >= len(cached_dirs):
            await query.answer(
                "Directory list changed, please refresh", show_alert=True
            )
            return
        subdir_name = cached_dirs[idx]

        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
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
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
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
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        try:
            pg = int(data[len(CB_DIR_PAGE) :])
        except ValueError:
            await query.answer("Invalid data")
            return
        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        if context.user_data is not None:
            context.user_data[BROWSE_PAGE_KEY] = pg

        msg_text, keyboard, subdirs = build_directory_browser(current_path, pg)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_CONFIRM:
        default_path = str(Path.cwd())
        selected_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        # Check if this was initiated from a thread bind flow
        pending_thread_id: int | None = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )

        # Validate: confirm button must come from the same topic that started browsing
        confirm_thread_id = _get_thread_id(update)
        if pending_thread_id is not None and confirm_thread_id != pending_thread_id:
            clear_browse_state(context.user_data)
            if context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
                context.user_data.pop("_pending_thread_text", None)
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return

        clear_browse_state(context.user_data)

        success, message, created_wname, created_wid = await tmux_manager.create_window(
            selected_path
        )
        if success:
            logger.info(
                "Window created: %s (id=%s) at %s (user=%d, thread=%s)",
                created_wname,
                created_wid,
                selected_path,
                user.id,
                pending_thread_id,
            )
            # Wait for Claude Code's SessionStart hook to register in session_map
            await session_manager.wait_for_session_map_entry(created_wid)

            if pending_thread_id is not None:
                # Thread bind flow: bind thread to newly created window
                session_manager.bind_thread(
                    user.id, pending_thread_id, created_wid, window_name=created_wname
                )

                # Rename the topic to match the window name
                resolved_chat = session_manager.resolve_chat_id(
                    user.id, pending_thread_id
                )
                try:
                    await context.bot.edit_forum_topic(
                        chat_id=resolved_chat,
                        message_thread_id=pending_thread_id,
                        name=created_wname,
                    )
                except Exception as e:
                    logger.debug(f"Failed to rename topic: {e}")

                await safe_edit(
                    query,
                    f"✅ {message}\n\nBound to this topic. Send messages here.",
                )

                # Send pending text if any
                pending_text = (
                    context.user_data.get("_pending_thread_text")
                    if context.user_data
                    else None
                )
                if pending_text:
                    logger.debug(
                        "Forwarding pending text to window %s (len=%d)",
                        created_wname,
                        len(pending_text),
                    )
                    if context.user_data is not None:
                        context.user_data.pop("_pending_thread_text", None)
                        context.user_data.pop("_pending_thread_id", None)
                    send_ok, send_msg = await session_manager.send_to_window(
                        created_wid,
                        pending_text,
                    )
                    if not send_ok:
                        logger.warning("Failed to forward pending text: %s", send_msg)
                        await safe_send(
                            context.bot,
                            resolved_chat,
                            f"❌ Failed to send pending message: {send_msg}",
                            message_thread_id=pending_thread_id,
                        )
                elif context.user_data is not None:
                    context.user_data.pop("_pending_thread_id", None)
            else:
                # Should not happen in topic-only mode, but handle gracefully
                await safe_edit(query, f"✅ {message}")
        else:
            await safe_edit(query, f"❌ {message}")
            if pending_thread_id is not None and context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
                context.user_data.pop("_pending_thread_text", None)
        await query.answer("Created" if success else "Failed")

    elif data == CB_DIR_CANCEL:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        clear_browse_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Window picker: bind existing window
    elif data.startswith(CB_WIN_BIND):
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        try:
            idx = int(data[len(CB_WIN_BIND) :])
        except ValueError:
            await query.answer("Invalid data")
            return

        cached_windows: list[str] = (
            context.user_data.get(UNBOUND_WINDOWS_KEY, []) if context.user_data else []
        )
        if idx < 0 or idx >= len(cached_windows):
            await query.answer("Window list changed, please retry", show_alert=True)
            return
        selected_wid = cached_windows[idx]

        # Verify window still exists
        w = await tmux_manager.find_window_by_id(selected_wid)
        if not w:
            display = session_manager.get_display_name(selected_wid)
            await query.answer(f"Window '{display}' no longer exists", show_alert=True)
            return

        thread_id = _get_thread_id(update)
        if thread_id is None:
            await query.answer("Not in a topic", show_alert=True)
            return

        display = w.window_name
        clear_window_picker_state(context.user_data)
        session_manager.bind_thread(
            user.id, thread_id, selected_wid, window_name=display
        )

        # Rename the topic to match the window name
        resolved_chat = session_manager.resolve_chat_id(user.id, thread_id)
        try:
            await context.bot.edit_forum_topic(
                chat_id=resolved_chat,
                message_thread_id=thread_id,
                name=display,
            )
        except Exception as e:
            logger.debug(f"Failed to rename topic: {e}")

        await safe_edit(
            query,
            f"✅ Bound to window `{display}`",
        )

        # Forward pending text if any
        pending_text = (
            context.user_data.get("_pending_thread_text") if context.user_data else None
        )
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_text", None)
            context.user_data.pop("_pending_thread_id", None)
        if pending_text:
            send_ok, send_msg = await session_manager.send_to_window(
                selected_wid, pending_text
            )
            if not send_ok:
                logger.warning("Failed to forward pending text: %s", send_msg)
                await safe_send(
                    context.bot,
                    resolved_chat,
                    f"❌ Failed to send pending message: {send_msg}",
                    message_thread_id=thread_id,
                )
        await query.answer("Bound")

    # Window picker: new session → transition to directory browser
    elif data == CB_WIN_NEW:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        # Preserve pending thread info, clear only picker state
        clear_window_picker_state(context.user_data)
        start_path = str(Path.cwd())
        msg_text, keyboard, subdirs = build_directory_browser(start_path)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    # Window picker: cancel
    elif data == CB_WIN_CANCEL:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        clear_window_picker_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Screenshot: Refresh
    elif data.startswith(CB_SCREENSHOT_REFRESH):
        window_id = data[len(CB_SCREENSHOT_REFRESH) :]
        w = await tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            return

        text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if not text:
            await query.answer("Failed to capture pane", show_alert=True)
            return

        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = _build_screenshot_keyboard(window_id)
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes), filename="screenshot.png"
                ),
                reply_markup=keyboard,
            )
            await query.answer("Refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh screenshot: {e}")
            await query.answer("Failed to refresh", show_alert=True)

    elif data == "noop":
        await query.answer()

    # Interactive UI: Up arrow
    elif data.startswith(CB_ASK_UP):
        window_id = data[len(CB_ASK_UP) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(w.window_id, "Up", enter=False, literal=False)
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer()

    # Interactive UI: Down arrow
    elif data.startswith(CB_ASK_DOWN):
        window_id = data[len(CB_ASK_DOWN) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Down", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer()

    # Interactive UI: Left arrow
    elif data.startswith(CB_ASK_LEFT):
        window_id = data[len(CB_ASK_LEFT) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Left", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer()

    # Interactive UI: Right arrow
    elif data.startswith(CB_ASK_RIGHT):
        window_id = data[len(CB_ASK_RIGHT) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Right", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer()

    # Interactive UI: Escape
    elif data.startswith(CB_ASK_ESC):
        window_id = data[len(CB_ASK_ESC) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Escape", enter=False, literal=False
            )
            await clear_interactive_msg(user.id, context.bot, thread_id)
        await query.answer("⎋ Esc")

    # Interactive UI: Enter
    elif data.startswith(CB_ASK_ENTER):
        window_id = data[len(CB_ASK_ENTER) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Enter", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer("⏎ Enter")

    # Interactive UI: Space
    elif data.startswith(CB_ASK_SPACE):
        window_id = data[len(CB_ASK_SPACE) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Space", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer("␣ Space")

    # Interactive UI: Tab
    elif data.startswith(CB_ASK_TAB):
        window_id = data[len(CB_ASK_TAB) :]
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(w.window_id, "Tab", enter=False, literal=False)
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer("⇥ Tab")

    # Interactive UI: refresh display
    elif data.startswith(CB_ASK_REFRESH):
        window_id = data[len(CB_ASK_REFRESH) :]
        thread_id = _get_thread_id(update)
        await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
        await query.answer("🔄")

    # Screenshot quick keys: send key to tmux window
    elif data.startswith(CB_KEYS_PREFIX):
        rest = data[len(CB_KEYS_PREFIX) :]
        colon_idx = rest.find(":")
        if colon_idx < 0:
            await query.answer("Invalid data")
            return
        key_id = rest[:colon_idx]
        window_id = rest[colon_idx + 1 :]

        key_info = _KEYS_SEND_MAP.get(key_id)
        if not key_info:
            await query.answer("Unknown key")
            return

        tmux_key, enter, literal = key_info
        w = await tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window not found", show_alert=True)
            return

        await tmux_manager.send_keys(
            w.window_id, tmux_key, enter=enter, literal=literal
        )
        await query.answer(_KEY_LABELS.get(key_id, key_id))

        # Refresh screenshot after key press
        await asyncio.sleep(0.5)
        text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if text:
            png_bytes = await text_to_image(text, with_ansi=True)
            keyboard = _build_screenshot_keyboard(window_id)
            try:
                await query.edit_message_media(
                    media=InputMediaDocument(
                        media=io.BytesIO(png_bytes),
                        filename="screenshot.png",
                    ),
                    reply_markup=keyboard,
                )
            except Exception:
                pass  # Screenshot unchanged or message too old


# --- Streaming response / notifications ---


async def handle_new_message(msg: NewMessage, bot: Bot) -> None:
    """Handle a new assistant message — enqueue for sequential processing.

    Messages are queued per-user to ensure status messages always appear last.
    Routes via thread_bindings to deliver to the correct topic.
    """
    status = "complete" if msg.is_complete else "streaming"
    logger.info(
        f"handle_new_message [{status}]: session={msg.session_id}, "
        f"text_len={len(msg.text)}"
    )

    # Find users whose thread-bound window matches this session
    active_users = await session_manager.find_users_for_session(msg.session_id)

    if not active_users:
        logger.info(f"No active users for session {msg.session_id}")
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
                image_data=msg.image_data,
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


# --- App lifecycle ---


async def post_init(application: Application) -> None:
    global session_monitor, _status_poll_task

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("start", "Show welcome message"),
        BotCommand("history", "Message history for this topic"),
        BotCommand("screenshot", "Terminal screenshot with control keys"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
        BotCommand("kill", "Kill session and delete topic"),
        BotCommand("unbind", "Unbind topic from session (keeps window running)"),
        BotCommand("usage", "Show Claude Code usage remaining"),
    ]
    # Add Claude Code slash commands
    for cmd_name, desc in CC_COMMANDS.items():
        bot_commands.append(BotCommand(cmd_name, desc))

    await application.bot.set_my_commands(bot_commands)

    # Re-resolve stale window IDs from persisted state against live tmux windows
    await session_manager.resolve_stale_ids()

    # Pre-fill global rate limiter bucket on restart.
    # AsyncLimiter starts at _level=0 (full burst capacity), but Telegram's
    # server-side counter persists across bot restarts.  Setting _level=max_rate
    # forces the bucket to start "full" so capacity drains in naturally (~1s).
    # AIORateLimiter has no per-private-chat limiter, so max_retries is the
    # primary protection (retry + pause all concurrent requests on 429).
    rate_limiter = application.bot.rate_limiter
    if rate_limiter and rate_limiter._base_limiter:
        rate_limiter._base_limiter._level = rate_limiter._base_limiter.max_rate
        logger.info("Pre-filled global rate limiter bucket")

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
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    application.add_handler(CommandHandler("unbind", unbind_command))
    application.add_handler(CommandHandler("usage", usage_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Topic closed event — auto-kill associated window
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CLOSED,
            topic_closed_handler,
        )
    )
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    # Photos: download and forward file path to Claude Code
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    # Catch-all: non-text content (stickers, voice, etc.)
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & ~filters.TEXT & ~filters.StatusUpdate.ALL,
            unsupported_content_handler,
        )
    )

    return application
