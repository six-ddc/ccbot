"""Recovery UI callback handlers.

Handles inline keyboard callbacks for dead window recovery:
  - CB_RECOVERY_FRESH: Create a fresh session in the same directory
  - CB_RECOVERY_CONTINUE: Continue most recent session (claude --continue)
  - CB_RECOVERY_RESUME: Show session picker, resume selected (claude --resume)
  - CB_RECOVERY_PICK: User picks a specific session from the resume list
  - CB_RECOVERY_BACK: Return to recovery options menu from session picker
  - CB_RECOVERY_CANCEL: Cancel recovery

Key function: handle_recovery_callback (uniform callback handler signature).
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_RECOVERY_BACK,
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_PICK,
    CB_RECOVERY_RESUME,
)
from .callback_helpers import get_thread_id
from .message_sender import safe_edit, safe_send
from .user_state import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    RECOVERY_SESSIONS,
    RECOVERY_WINDOW_ID,
)

logger = logging.getLogger(__name__)

_MAX_RESUME_SESSIONS = 6


@dataclass
class _SessionEntry:
    """A resumable session discovered from sessions-index."""

    session_id: str
    summary: str


def build_recovery_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for dead window recovery options."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f195 Fresh",
                    callback_data=f"{CB_RECOVERY_FRESH}{window_id}"[:64],
                ),
                InlineKeyboardButton(
                    "\u25b6 Continue",
                    callback_data=f"{CB_RECOVERY_CONTINUE}{window_id}"[:64],
                ),
                InlineKeyboardButton(
                    "\U0001f4c2 Resume",
                    callback_data=f"{CB_RECOVERY_RESUME}{window_id}"[:64],
                ),
            ],
            [
                InlineKeyboardButton("\u2716 Cancel", callback_data=CB_RECOVERY_CANCEL),
            ],
        ]
    )


def _build_resume_picker_keyboard(
    sessions: list[_SessionEntry],
    window_id: str,
) -> InlineKeyboardMarkup:
    """Build inline keyboard listing recent sessions for resume."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, entry in enumerate(sessions[:_MAX_RESUME_SESSIONS]):
        label = entry.summary[:40] or entry.session_id[:12]
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"{CB_RECOVERY_PICK}{idx}"[:64],
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "\u2b05 Back",
                callback_data=f"{CB_RECOVERY_BACK}{window_id}"[:64],
            ),
            InlineKeyboardButton("\u2716 Cancel", callback_data=CB_RECOVERY_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def scan_sessions_for_cwd(cwd: str) -> list[_SessionEntry]:
    """Scan sessions-index files for sessions matching a working directory.

    Returns up to _MAX_RESUME_SESSIONS entries, most-recent file first.
    """
    if not config.claude_projects_path.exists():
        return []

    try:
        resolved_cwd = str(Path(cwd).resolve())
    except OSError:
        return []

    candidates: list[tuple[float, _SessionEntry]] = []

    for project_dir in config.claude_projects_path.iterdir():
        if not project_dir.is_dir():
            continue
        index_file = project_dir / "sessions-index.json"
        if not index_file.exists():
            continue
        try:
            index_data = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        except OSError:
            continue

        original_path = index_data.get("originalPath", "")
        for entry in index_data.get("entries", []):
            session_id = entry.get("sessionId", "")
            full_path = entry.get("fullPath", "")
            project_path = entry.get("projectPath", original_path)
            if not session_id or not full_path:
                continue

            try:
                norm_pp = str(Path(project_path).resolve())
            except OSError:
                norm_pp = project_path

            if norm_pp != resolved_cwd:
                continue

            file_path = Path(full_path)
            if not file_path.exists():
                continue

            # Use file mtime as recency proxy
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = 0.0

            summary = entry.get("summary", "") or session_id[:12]
            candidates.append((mtime, _SessionEntry(session_id, summary)))

    # Sort by mtime descending (most recent first)
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [entry for _, entry in candidates[:_MAX_RESUME_SESSIONS]]


async def handle_recovery_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle recovery UI callbacks."""
    if data.startswith(CB_RECOVERY_BACK):
        await _handle_back(query, data, update, context)
    elif data.startswith(CB_RECOVERY_FRESH):
        await _handle_fresh(query, user_id, data, update, context)
    elif data.startswith(CB_RECOVERY_CONTINUE):
        await _handle_continue(query, user_id, data, update, context)
    elif data.startswith(CB_RECOVERY_RESUME):
        await _handle_resume(query, user_id, data, update, context)
    elif data.startswith(CB_RECOVERY_PICK):
        await _handle_resume_pick(query, user_id, data, update, context)
    elif data == CB_RECOVERY_CANCEL:
        await _handle_cancel(query, update, context)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _validate_recovery_state(
    data_suffix: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int, str, str] | None:
    """Validate common recovery preconditions.

    Supports two paths:
      1. Text-handler path: PENDING_THREAD_ID and RECOVERY_WINDOW_ID in user_data.
      2. Proactive notification path: no user_data state, validate via binding.

    Returns (thread_id, old_window_id, cwd) on success, or None on failure
    (caller should return early and call query.answer).
    """
    thread_id = get_thread_id(update)
    if thread_id is None:
        return None

    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return None

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    stored_wid = (
        context.user_data.get(RECOVERY_WINDOW_ID) if context.user_data else None
    )

    if pending_tid is not None:
        # Text-handler path: validate stored state
        if thread_id != pending_tid or stored_wid != data_suffix:
            return None
    else:
        # Proactive notification path: validate via session_manager binding
        bound_wid = session_manager.get_window_for_thread(user_id, thread_id)
        if bound_wid != data_suffix:
            return None
        # Set up recovery state for downstream handlers
        if context.user_data is not None:
            context.user_data[PENDING_THREAD_ID] = thread_id
            context.user_data[RECOVERY_WINDOW_ID] = data_suffix

    ws = session_manager.get_window_state(data_suffix)
    cwd = ws.cwd or ""
    return thread_id, data_suffix, cwd


def _clear_recovery_state(user_data: dict | None) -> None:
    """Remove all recovery-related keys from user_data."""
    if user_data is None:
        return
    for key in (
        PENDING_THREAD_ID,
        PENDING_THREAD_TEXT,
        RECOVERY_WINDOW_ID,
        RECOVERY_SESSIONS,
    ):
        user_data.pop(key, None)


async def _create_and_bind_window(
    query: CallbackQuery,
    user_id: int,
    thread_id: int,
    cwd: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    claude_args: str = "",
    success_label: str = "Session started.",
) -> bool:
    """Create a new tmux window, bind it, rename topic, forward pending text.

    Returns True on success, False on failure.
    """
    # Unbind old dead window and clear dead-notification tracking
    session_manager.unbind_thread(user_id, thread_id)
    from .status_polling import clear_dead_notification

    clear_dead_notification(user_id, thread_id)

    success, message, created_wname, created_wid = await tmux_manager.create_window(
        cwd, claude_args=claude_args
    )
    if not success:
        await safe_edit(query, f"\u274c {message}")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return False

    await session_manager.wait_for_session_map_entry(created_wid)
    session_manager.bind_thread(
        user_id, thread_id, created_wid, window_name=created_wname
    )

    try:
        await context.bot.edit_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=created_wname,
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    await safe_edit(query, f"\u2705 {message}\n\n{success_label}")

    # Forward pending text
    pending_text = (
        context.user_data.get(PENDING_THREAD_TEXT) if context.user_data else None
    )
    _clear_recovery_state(context.user_data)
    if pending_text:
        send_ok, send_msg = await session_manager.send_to_window(
            created_wid, pending_text
        )
        if not send_ok:
            logger.warning("Failed to forward pending text: %s", send_msg)
            await safe_send(
                context.bot,
                session_manager.resolve_chat_id(user_id, thread_id),
                f"\u274c Failed to send pending message: {send_msg}",
                message_thread_id=thread_id,
            )
    await query.answer("Created")
    return True


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------


async def _handle_back(
    query: CallbackQuery,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_BACK: return to the recovery options menu."""
    window_id = data[len(CB_RECOVERY_BACK) :]
    validated = _validate_recovery_state(window_id, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return
    kb = build_recovery_keyboard(window_id)
    await safe_edit(
        query, "\u26a0\ufe0f Session ended. Choose an option:", reply_markup=kb
    )
    await query.answer()


async def _handle_fresh(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_FRESH: create fresh session in same directory."""
    old_wid = data[len(CB_RECOVERY_FRESH) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    thread_id, _, cwd = validated
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return

    await _create_and_bind_window(
        query,
        user_id,
        thread_id,
        cwd,
        context,
        success_label="Fresh session started.",
    )


async def _handle_continue(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_CONTINUE: resume most recent session via --continue."""
    old_wid = data[len(CB_RECOVERY_CONTINUE) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    thread_id, _, cwd = validated
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return

    await _create_and_bind_window(
        query,
        user_id,
        thread_id,
        cwd,
        context,
        claude_args="--continue",
        success_label="Continuing previous session.",
    )


async def _handle_resume(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_RESUME: show session picker for --resume."""
    old_wid = data[len(CB_RECOVERY_RESUME) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    _, _, cwd = validated
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return

    sessions = scan_sessions_for_cwd(cwd)
    if not sessions:
        await query.answer("No sessions found for this directory", show_alert=True)
        return

    # Store session list for pick callback
    if context.user_data is not None:
        context.user_data[RECOVERY_SESSIONS] = [
            {"session_id": s.session_id, "summary": s.summary} for s in sessions
        ]

    keyboard = _build_resume_picker_keyboard(sessions, old_wid)
    await safe_edit(
        query,
        f"\U0001f4c2 Select a session to resume:\n(`{cwd}`)",
        reply_markup=keyboard,
    )
    await query.answer()


async def _handle_resume_pick(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_PICK: user selected a session from resume picker."""
    idx_str = data[len(CB_RECOVERY_PICK) :]
    try:
        idx = int(idx_str)
    except ValueError:
        await query.answer("Invalid selection", show_alert=True)
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    if pending_tid is None or thread_id != pending_tid:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    stored_sessions = (
        context.user_data.get(RECOVERY_SESSIONS) if context.user_data else None
    )
    if not stored_sessions or idx < 0 or idx >= len(stored_sessions):
        await query.answer("Invalid session index", show_alert=True)
        return

    picked = stored_sessions[idx]
    session_id = picked["session_id"]

    old_wid = context.user_data.get(RECOVERY_WINDOW_ID) if context.user_data else None
    if not old_wid:
        await query.answer("Stale recovery state", show_alert=True)
        return

    ws = session_manager.get_window_state(old_wid)
    cwd = ws.cwd or ""
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return

    await _create_and_bind_window(
        query,
        user_id,
        thread_id,
        cwd,
        context,
        claude_args=f"--resume {session_id}",
        success_label=f"Resuming session: {picked['summary'][:40]}",
    )


async def _handle_cancel(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_CANCEL: cancel recovery."""
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    if pending_tid is not None and thread_id != pending_tid:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    _clear_recovery_state(context.user_data)
    await safe_edit(query, "Cancelled. Send a message to try again.")
    await query.answer("Cancelled")
