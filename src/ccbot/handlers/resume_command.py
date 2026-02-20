"""Resume command — browse and resume past Claude Code sessions.

Implements /resume: scans all sessions-index files under ~/.claude/projects/,
groups them by project directory, and shows a paginated inline keyboard.
On selection, creates a tmux window with `claude --resume <id>` and binds
the current topic.

Key functions:
  - resume_command: /resume handler
  - handle_resume_command_callback: callback dispatcher for resume UI
  - scan_all_sessions: discover all resumable sessions across all projects
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..config import config
from ..providers import get_provider
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import CB_RESUME_CANCEL, CB_RESUME_PAGE, CB_RESUME_PICK
from .callback_helpers import get_thread_id
from .message_sender import safe_edit, safe_reply
from .user_state import RESUME_SESSIONS

logger = logging.getLogger(__name__)

_SESSIONS_PER_PAGE = 6

_IndexParseError = (json.JSONDecodeError, OSError)


@dataclass
class ResumeEntry:
    """A resumable session discovered from sessions-index."""

    session_id: str
    summary: str
    cwd: str


def scan_all_sessions() -> list[ResumeEntry]:
    """Scan all sessions-index files for resumable sessions.

    Returns entries sorted by file mtime (most recent first),
    deduplicated by session_id.
    """
    if not config.claude_projects_path.exists():
        return []

    candidates: list[tuple[float, ResumeEntry]] = []
    seen_ids: set[str] = set()

    for project_dir in config.claude_projects_path.iterdir():
        if not project_dir.is_dir():
            continue
        index_file = project_dir / "sessions-index.json"
        if not index_file.exists():
            continue
        try:
            index_data = json.loads(index_file.read_text(encoding="utf-8"))
        except _IndexParseError:
            continue

        original_path = index_data.get("originalPath", "")
        for entry in index_data.get("entries", []):
            session_id = entry.get("sessionId", "")
            full_path = entry.get("fullPath", "")
            if not session_id or not full_path or session_id in seen_ids:
                continue

            file_path = Path(full_path)
            if not file_path.exists():
                continue

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = 0.0

            cwd = entry.get("projectPath", original_path)
            summary = entry.get("summary", "") or session_id[:12]
            seen_ids.add(session_id)
            candidates.append((mtime, ResumeEntry(session_id, summary, cwd)))

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [entry for _, entry in candidates]


def _build_resume_keyboard(
    sessions: list[dict[str, str]],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Build inline keyboard for resume session picker with pagination."""
    total = len(sessions)
    start = page * _SESSIONS_PER_PAGE
    end = min(start + _SESSIONS_PER_PAGE, total)
    page_sessions = sessions[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    current_cwd = ""
    for idx_offset, entry in enumerate(page_sessions):
        global_idx = start + idx_offset
        cwd = entry.get("cwd", "")
        # Show project header when cwd changes
        if cwd != current_cwd:
            current_cwd = cwd
            short_path = Path(cwd).name if cwd else "unknown"
            rows.append(
                [
                    InlineKeyboardButton(
                        f"\U0001f4c1 {short_path}",
                        callback_data="noop",
                    )
                ]
            )
        label = entry.get("summary", "")[:40] or entry["session_id"][:12]
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"{CB_RESUME_PICK}{global_idx}"[:64],
                )
            ]
        )

    # Pagination row
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "\u2b05 Prev",
                callback_data=f"{CB_RESUME_PAGE}{page - 1}"[:64],
            )
        )
    total_pages = (total + _SESSIONS_PER_PAGE - 1) // _SESSIONS_PER_PAGE
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "Next \u27a1",
                callback_data=f"{CB_RESUME_PAGE}{page + 1}"[:64],
            )
        )
    nav_buttons.append(
        InlineKeyboardButton("\u2716 Cancel", callback_data=CB_RESUME_CANCEL)
    )
    rows.append(nav_buttons)

    return InlineKeyboardMarkup(rows)


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume — show all resumable sessions grouped by project."""
    if not update.message:
        return

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return

    if not get_provider().capabilities.supports_resume:
        await safe_reply(
            update.message,
            "\u274c Resume is not supported by the current provider.",
        )
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(
            update.message,
            "\u274c Please use /resume in a named topic.",
        )
        return

    sessions = scan_all_sessions()
    if not sessions:
        await safe_reply(update.message, "\u274c No past sessions found.")
        return

    session_dicts = [
        {"session_id": s.session_id, "summary": s.summary, "cwd": s.cwd}
        for s in sessions
    ]
    if context.user_data is not None:
        context.user_data[RESUME_SESSIONS] = session_dicts

    keyboard = _build_resume_keyboard(session_dicts, page=0)
    await safe_reply(
        update.message,
        "\U0001f4c2 Select a session to resume:",
        reply_markup=keyboard,
    )


async def handle_resume_command_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Dispatch resume command callbacks."""
    if data.startswith(CB_RESUME_PICK):
        await _handle_pick(query, user_id, data, update, context)
    elif data.startswith(CB_RESUME_PAGE):
        await _handle_page(query, user_id, data, update, context)
    elif data == CB_RESUME_CANCEL:
        await _handle_cancel(query, context)


async def _handle_pick(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle session selection from the resume picker."""
    idx_str = data[len(CB_RESUME_PICK) :]
    try:
        idx = int(idx_str)
    except ValueError:
        await query.answer("Invalid selection", show_alert=True)
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return

    stored = context.user_data.get(RESUME_SESSIONS) if context.user_data else None
    if not stored or idx < 0 or idx >= len(stored):
        await query.answer("Invalid session index", show_alert=True)
        return

    picked = stored[idx]
    session_id = picked["session_id"]
    cwd = picked.get("cwd", "")

    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Project directory no longer exists.")
        _clear_resume_state(context.user_data)
        await query.answer("Failed")
        return

    # Unbind existing window if any
    old_window_id = session_manager.get_window_for_thread(user_id, thread_id)
    if old_window_id:
        session_manager.unbind_thread(user_id, thread_id)
        from .status_polling import clear_dead_notification

        clear_dead_notification(user_id, thread_id)

    launch_args = get_provider().make_launch_args(resume_id=session_id)
    success, message, created_wname, created_wid = await tmux_manager.create_window(
        cwd, claude_args=launch_args
    )
    if not success:
        await safe_edit(query, f"\u274c {message}")
        _clear_resume_state(context.user_data)
        await query.answer("Failed")
        return

    await session_manager.wait_for_session_map_entry(created_wid)
    session_manager.bind_thread(
        user_id, thread_id, created_wid, window_name=created_wname
    )

    # Store group chat_id for routing
    chat = query.message.chat if query.message else None
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user_id, thread_id, chat.id)

    # Rename topic to match the window
    try:
        await context.bot.edit_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=created_wname,
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    summary_short = picked.get("summary", "")[:40]
    await safe_edit(
        query,
        f"\u2705 Resuming session: {summary_short}\n\U0001f4c2 `{cwd}`",
    )
    _clear_resume_state(context.user_data)
    await query.answer("Resumed")


async def _handle_page(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    _update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle pagination in resume picker."""
    page_str = data[len(CB_RESUME_PAGE) :]
    try:
        page = int(page_str)
    except ValueError:
        await query.answer("Invalid page", show_alert=True)
        return

    stored = context.user_data.get(RESUME_SESSIONS) if context.user_data else None
    if not stored:
        await query.answer("No sessions available", show_alert=True)
        return

    keyboard = _build_resume_keyboard(stored, page=page)
    await safe_edit(
        query,
        "\U0001f4c2 Select a session to resume:",
        reply_markup=keyboard,
    )
    await query.answer()


async def _handle_cancel(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle cancel in resume picker."""
    _clear_resume_state(context.user_data)
    await safe_edit(query, "Resume cancelled.")
    await query.answer("Cancelled")


def _clear_resume_state(user_data: dict | None) -> None:
    """Remove resume-related keys from user_data."""
    if user_data is None:
        return
    user_data.pop(RESUME_SESSIONS, None)
