"""Interactive UI handling for Claude Code prompts (AskUserQuestion, etc.).

Core responsibilities:
  - Detect and extract interactive UI content from terminal
  - Send interactive UI with navigation keyboard
  - Track interactive mode state per user
  - Handle keyboard navigation (arrows, ESC, Enter, refresh)

Key functions: handle_interactive_ui(), clear_interactive_msg(), get_interactive_window()
"""

import logging

from telegram import Bot

from .message_sender import NO_LINK_PREVIEW, rate_limit_send
from .terminal_parser import extract_interactive_content, is_interactive_ui
from .tmux_manager import tmux_manager
from .ui_components import build_interactive_keyboard

logger = logging.getLogger(__name__)

# Track interactive UI message IDs: user_id -> message_id
_interactive_msgs: dict[int, int] = {}

# Track interactive mode: user_id -> window_name (None if not in interactive mode)
_interactive_mode: dict[int, str] = {}


async def handle_interactive_ui(
    bot: Bot,
    user_id: int,
    window_name: str,
) -> bool:
    """Capture terminal and send interactive UI content to user.

    Handles AskUserQuestion, ExitPlanMode, Permission Prompt, and
    RestoreCheckpoint UIs. Returns True if UI was detected and sent,
    False otherwise.
    """
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        return False

    # Capture plain text (no ANSI colors)
    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return False

    # Quick check if it looks like an interactive UI
    if not is_interactive_ui(pane_text):
        return False

    # Extract content between separators
    content = extract_interactive_content(pane_text)
    if not content:
        return False

    # Build message with navigation keyboard
    keyboard = build_interactive_keyboard(window_name, ui_name=content.name)

    # Send as plain text (no markdown conversion)
    text = content.content

    # Check if we have an existing interactive message to edit
    existing_msg_id = _interactive_msgs.get(user_id)
    if existing_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=keyboard,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _interactive_mode[user_id] = window_name
            return True
        except Exception:
            # Message unchanged or other error - silently ignore, don't send new
            return True

    # Send new message
    await rate_limit_send(user_id)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=keyboard,
            link_preview_options=NO_LINK_PREVIEW,
        )
        _interactive_msgs[user_id] = sent.message_id
        _interactive_mode[user_id] = window_name
    except Exception as e:
        logger.error(f"Failed to send interactive UI to {user_id}: {e}")
        return False

    return True


async def clear_interactive_msg(user_id: int, bot: Bot | None = None) -> None:
    """Clear tracked interactive message, delete from chat, and exit interactive mode."""
    msg_id = _interactive_msgs.pop(user_id, None)
    _interactive_mode.pop(user_id, None)
    if bot and msg_id:
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass  # Message may already be deleted or too old


def get_interactive_window(user_id: int) -> str | None:
    """Get the window name for user's interactive mode."""
    return _interactive_mode.get(user_id)
