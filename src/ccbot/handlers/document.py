"""Telegram document (file) upload handler.

Downloads files sent to a bound topic into ``~/.ccbot/documents/`` and forwards
the saved path to the Claude Code session as ``(file attached: <path>)``.

The shared per-update helpers (``is_user_allowed``, ``_get_thread_id``) are
imported lazily from bot.py inside the handler to avoid a circular import.
"""

import logging
import re
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from ..utils import ccbot_dir
from .message_queue import clear_status_msg_info
from .message_sender import safe_reply

logger = logging.getLogger(__name__)

# Incoming files are saved here before the path is forwarded to Claude Code.
_DOCS_DIR = ccbot_dir() / "documents"
_DOCS_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Bot API caps bot downloads (getFile) at 20 MB; larger files cannot
# be fetched and must be rejected before attempting the download.
_MAX_DOC_BYTES = 20 * 1024 * 1024


def _format_size(num_bytes: int) -> str:
    """Render a byte count as a human-readable size (e.g. '24.3 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_filename(name: str) -> str:
    """Sanitize a Telegram-provided filename for safe use as a path component."""
    # Keep only the basename, strip path separators, allow a conservative set.
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Avoid empty / dotfile-only names
    return name.strip("._") or "file"


async def document_handler(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents (PDF, etc.): download and forward file path to Claude Code."""
    # Lazy import keeps bot.py's layout intact (avoids a circular import).
    from ..bot import _get_thread_id, is_user_allowed

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.document:
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

    # Reject oversized files before downloading: Telegram won't let bots fetch
    # files larger than 20 MB, so check the advertised size up front.
    doc = update.message.document
    if doc.file_size and doc.file_size > _MAX_DOC_BYTES:
        await safe_reply(
            update.message,
            f"❌ File is too large ({_format_size(doc.file_size)}). "
            f"Telegram only lets bots download files up to {_format_size(_MAX_DOC_BYTES)}.",
        )
        return

    # Download the document, preserving its original name where possible
    original = doc.file_name or f"{doc.file_unique_id}"
    filename = f"{int(time.time())}_{_safe_filename(original)}"
    file_path = _DOCS_DIR / filename

    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(file_path)
    except BadRequest as exc:
        # Telegram rejects getFile for files above its download cap, even when
        # file_size was missing/under-reported on the message.
        logger.warning("Document download failed for user %d: %s", user.id, exc)
        await safe_reply(
            update.message,
            f"❌ Could not download the file. It may exceed Telegram's "
            f"{_format_size(_MAX_DOC_BYTES)} download limit for bots.",
        )
        return

    # Build the message to send to Claude Code
    caption = update.message.caption or ""
    if caption:
        text_to_send = f"{caption}\n\n(file attached: {file_path})"
    else:
        text_to_send = f"(file attached: {file_path})"

    # Cosmetic typing indicator must never abort before the file path is injected
    # into tmux — a transient TimedOut would silently drop the upload.
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception as e:
        logger.warning("send_action(TYPING) failed, continuing to injection: %s", e)
    clear_status_msg_info(user.id, thread_id)

    success, message = await session_manager.send_to_window(wid, text_to_send)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    # Confirm to user
    await safe_reply(
        update.message, f"📎 File sent to Claude Code: {doc.file_name or filename}"
    )
