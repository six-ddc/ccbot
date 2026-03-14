"""Interactive UI handling for Claude Code prompts.

Handles interactive terminal UIs displayed by Claude Code:
  - AskUserQuestion: Multi-choice question prompts
  - ExitPlanMode: Plan mode exit confirmation
  - Permission Prompt: Tool permission requests
  - RestoreCheckpoint: Checkpoint restoration selection

Provides:
  - Keyboard navigation (up/down/left/right/enter/esc)
  - Direct numeric selection for option lists (1..N)
  - Terminal capture and display
  - Interactive mode tracking per user and thread

State dicts are keyed by (user_id, thread_id_or_0) for Telegram topic support.
"""

import logging
import re
import time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from ..session import session_manager
from ..terminal_parser import extract_interactive_content, is_interactive_ui
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SELECT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)
from .message_sender import NO_LINK_PREVIEW

logger = logging.getLogger(__name__)

# Tool names that trigger interactive UI via JSONL (terminal capture + inline keyboard)
INTERACTIVE_TOOL_NAMES = frozenset({"AskUserQuestion", "ExitPlanMode"})

# Track interactive UI message IDs: (user_id, thread_id_or_0) -> message_id
_interactive_msgs: dict[tuple[int, int], int] = {}

# Track interactive mode: (user_id, thread_id_or_0) -> window_id
_interactive_mode: dict[tuple[int, int], str] = {}

# Track parsed option state: (user_id, thread_id_or_0) -> (selected_index, total_options)
_interactive_choices: dict[tuple[int, int], tuple[int, int]] = {}
# Track latest rendered interactive payload to suppress duplicate re-sends.
# Value: (text, monotonic_timestamp)
_interactive_last_render: dict[tuple[int, int], tuple[str, float]] = {}

_INTERACTIVE_RESEND_COOLDOWN_SECONDS = 30.0

_OPTION_NUMBER_RE = re.compile(r"^(?P<num>\d{1,2})\.\s+(?P<label>.+)$")
_OPTION_MARK_RE = re.compile(r"^(?P<mark>[☐✔☒●○])\s+(?P<label>.+)$")


def get_interactive_window(user_id: int, thread_id: int | None = None) -> str | None:
    """Get the window_id for user's interactive mode."""
    return _interactive_mode.get((user_id, thread_id or 0))


def set_interactive_mode(
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> None:
    """Set interactive mode for a user."""
    logger.debug(
        "Set interactive mode: user=%d, window_id=%s, thread=%s",
        user_id,
        window_id,
        thread_id,
    )
    _interactive_mode[(user_id, thread_id or 0)] = window_id


def clear_interactive_mode(user_id: int, thread_id: int | None = None) -> None:
    """Clear interactive mode for a user (without deleting message)."""
    logger.debug("Clear interactive mode: user=%d, thread=%s", user_id, thread_id)
    ikey = (user_id, thread_id or 0)
    _interactive_mode.pop(ikey, None)
    _interactive_choices.pop(ikey, None)
    _interactive_last_render.pop(ikey, None)


def get_interactive_msg_id(user_id: int, thread_id: int | None = None) -> int | None:
    """Get the interactive message ID for a user."""
    return _interactive_msgs.get((user_id, thread_id or 0))


def get_interactive_choice_state(
    user_id: int,
    thread_id: int | None = None,
) -> tuple[int, int] | None:
    """Get (selected_index, total_options) parsed from latest interactive UI."""
    return _interactive_choices.get((user_id, thread_id or 0))


def _extract_interactive_choices(content: str) -> tuple[int, int] | None:
    """Extract option count and selected index from interactive pane text."""
    selected_idx: int | None = None
    total = 0

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        has_cursor = stripped.startswith(("❯", "›", ">", "←", "→"))
        normalized = stripped.lstrip("❯›>←→ ").strip()

        num_match = _OPTION_NUMBER_RE.match(normalized)
        if num_match:
            total += 1
            if has_cursor:
                selected_idx = total
            continue

        mark_match = _OPTION_MARK_RE.match(normalized)
        if mark_match:
            total += 1
            marker = mark_match.group("mark")
            if has_cursor or marker in {"✔", "☒", "●"}:
                selected_idx = total

    if total < 2:
        return None
    return (selected_idx or 1, total)


def _build_interactive_keyboard(
    window_id: str,
    ui_name: str = "",
    option_count: int = 0,
) -> InlineKeyboardMarkup:
    """Build keyboard for interactive UI navigation.

    ``ui_name`` controls the layout: ``RestoreCheckpoint`` omits ←/→ keys
    since only vertical selection is needed.
    """
    vertical_only = ui_name == "RestoreCheckpoint"

    rows: list[list[InlineKeyboardButton]] = []
    # Row 1: directional keys
    rows.append(
        [
            InlineKeyboardButton(
                "␣ Space", callback_data=f"{CB_ASK_SPACE}{window_id}"[:64]
            ),
            InlineKeyboardButton("↑", callback_data=f"{CB_ASK_UP}{window_id}"[:64]),
            InlineKeyboardButton(
                "⇥ Tab", callback_data=f"{CB_ASK_TAB}{window_id}"[:64]
            ),
        ]
    )
    if vertical_only:
        rows.append(
            [
                InlineKeyboardButton(
                    "↓", callback_data=f"{CB_ASK_DOWN}{window_id}"[:64]
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "←", callback_data=f"{CB_ASK_LEFT}{window_id}"[:64]
                ),
                InlineKeyboardButton(
                    "↓", callback_data=f"{CB_ASK_DOWN}{window_id}"[:64]
                ),
                InlineKeyboardButton(
                    "→", callback_data=f"{CB_ASK_RIGHT}{window_id}"[:64]
                ),
            ]
        )
    # Optional numeric quick-select rows (1..N, max 9).
    if option_count > 1:
        max_buttons = min(option_count, 9)
        digits = [
            InlineKeyboardButton(
                str(i),
                callback_data=f"{CB_ASK_SELECT}{i}:{window_id}"[:64],
            )
            for i in range(1, max_buttons + 1)
        ]
        for i in range(0, len(digits), 5):
            rows.append(digits[i : i + 5])

    # Row 2/3: action keys
    rows.append(
        [
            InlineKeyboardButton(
                "⎋ Esc", callback_data=f"{CB_ASK_ESC}{window_id}"[:64]
            ),
            InlineKeyboardButton(
                "🔄", callback_data=f"{CB_ASK_REFRESH}{window_id}"[:64]
            ),
            InlineKeyboardButton(
                "⏎ Enter", callback_data=f"{CB_ASK_ENTER}{window_id}"[:64]
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def handle_interactive_ui(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> bool:
    """Capture terminal and send interactive UI content to user.

    Handles AskUserQuestion, ExitPlanMode, Permission Prompt, and
    RestoreCheckpoint UIs. Returns True if UI was detected and sent,
    False otherwise.
    """
    ikey = (user_id, thread_id or 0)
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        return False

    # Capture plain text (no ANSI colors)
    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        logger.debug("No pane text captured for window_id %s", window_id)
        return False

    # Quick check if it looks like an interactive UI
    if not is_interactive_ui(pane_text):
        logger.debug(
            "No interactive UI detected in window_id %s (last 3 lines: %s)",
            window_id,
            pane_text.strip().split("\n")[-3:],
        )
        return False

    # Extract content between separators
    content = extract_interactive_content(pane_text)
    if not content:
        return False

    text = content.content
    choice_state = _extract_interactive_choices(text)
    if choice_state:
        _interactive_choices[ikey] = choice_state
    else:
        _interactive_choices.pop(ikey, None)

    # Build message with navigation keyboard
    keyboard = _build_interactive_keyboard(
        window_id,
        ui_name=content.name,
        option_count=choice_state[1] if choice_state else 0,
    )

    # Send as plain text (no markdown conversion)

    # Build thread kwargs for send_message
    thread_kwargs: dict[str, int] = {}
    if thread_id is not None:
        thread_kwargs["message_thread_id"] = thread_id

    # Check if we have an existing interactive message to edit
    existing_msg_id = _interactive_msgs.get(ikey)
    last_render = _interactive_last_render.get(ikey)
    if last_render and last_render[0] == text:
        # If payload is unchanged, avoid redundant edits/sends from polling.
        if existing_msg_id:
            _interactive_mode[ikey] = window_id
            _interactive_last_render[ikey] = (text, time.monotonic())
            return True
        # Message id may be temporarily absent after an edit race/failure.
        # Suppress duplicate sends of identical payload for a short cooldown.
        if (
            time.monotonic() - last_render[1]
            < _INTERACTIVE_RESEND_COOLDOWN_SECONDS
        ):
            _interactive_mode[ikey] = window_id
            return True

    if existing_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=keyboard,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _interactive_mode[ikey] = window_id
            _interactive_last_render[ikey] = (text, time.monotonic())
            return True
        except BadRequest as e:
            # Polling may re-render identical UI every second; Telegram rejects
            # no-op edits with "message is not modified". Treat it as success
            # to avoid posting duplicate interactive messages.
            if "message is not modified" in str(e).lower():
                _interactive_mode[ikey] = window_id
                _interactive_last_render[ikey] = (text, time.monotonic())
                return True
            # Edit failed (message deleted, etc.) - clear stale msg_id and send new
            logger.debug(
                "Edit failed for interactive msg %s, sending new", existing_msg_id
            )
            _interactive_msgs.pop(ikey, None)
            # Fall through to send new message
        except Exception:
            # Edit failed (message deleted, etc.) - clear stale msg_id and send new
            logger.debug(
                "Edit failed for interactive msg %s, sending new", existing_msg_id
            )
            _interactive_msgs.pop(ikey, None)
            # Fall through to send new message

    # Send new message (plain text — terminal content is not markdown)
    logger.info(
        "Sending interactive UI to user %d for window_id %s", user_id, window_id
    )
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            link_preview_options=NO_LINK_PREVIEW,
            **thread_kwargs,  # type: ignore[arg-type]
        )
    except Exception as e:
        logger.error("Failed to send interactive UI: %s", e)
        return False
    if sent:
        _interactive_msgs[ikey] = sent.message_id
        _interactive_mode[ikey] = window_id
        _interactive_last_render[ikey] = (text, time.monotonic())
        return True
    return False


async def clear_interactive_msg(
    user_id: int,
    bot: Bot | None = None,
    thread_id: int | None = None,
) -> None:
    """Clear tracked interactive message, delete from chat, and exit interactive mode."""
    ikey = (user_id, thread_id or 0)
    msg_id = _interactive_msgs.pop(ikey, None)
    _interactive_mode.pop(ikey, None)
    _interactive_choices.pop(ikey, None)
    _interactive_last_render.pop(ikey, None)
    logger.debug(
        "Clear interactive msg: user=%d, thread=%s, msg_id=%s",
        user_id,
        thread_id,
        msg_id,
    )
    if bot and msg_id:
        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass  # Message may already be deleted or too old
