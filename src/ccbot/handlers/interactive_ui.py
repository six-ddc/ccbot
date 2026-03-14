"""Interactive UI handling for Claude Code and Codex prompts.

Supports two interaction styles:
  - Terminal-driven UIs captured from tmux (Claude AskUserQuestion, ExitPlanMode,
    permission prompts, settings, checkpoint restore)
  - Codex `request_user_input` prompts rendered natively in Telegram

State dicts are keyed by (user_id, thread_id_or_0) for Telegram topic support.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ..session import session_manager
from ..terminal_parser import extract_interactive_content, is_interactive_ui
from ..tmux_manager import tmux_manager
from ..transcript_parser import CodexPromptPayload, CodexPromptQuestion
from .callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    CB_CODEX_PROMPT_CANCEL,
    CB_CODEX_PROMPT_OPTION,
    CB_CODEX_PROMPT_OTHER,
    CB_CODEX_PROMPT_REFRESH,
)
from .message_sender import NO_LINK_PREVIEW

logger = logging.getLogger(__name__)
_RE_CODEX_PROMPT_FOCUS = re.compile(r"^\s*›\s*(\d+)\.")

# Tool names that trigger interactive UI via JSONL (terminal capture + inline keyboard)
INTERACTIVE_TOOL_NAMES = frozenset(
    {"AskUserQuestion", "ExitPlanMode", "request_user_input"}
)

# Track interactive UI message IDs: (user_id, thread_id_or_0) -> message_id
_interactive_msgs: dict[tuple[int, int], int] = {}

# Track interactive mode: (user_id, thread_id_or_0) -> window_id
_interactive_mode: dict[tuple[int, int], str] = {}

# Track the last terminal UI text sent for a user/thread to avoid duplicate edits
_interactive_text_cache: dict[tuple[int, int], str] = {}


@dataclass
class CodexPromptState:
    """Telegram-side state for a pending Codex request_user_input prompt."""

    window_id: str
    tool_use_id: str
    prompt: CodexPromptPayload
    question_index: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    awaiting_notes_text: bool = False


_codex_prompt_states: dict[tuple[int, int], CodexPromptState] = {}


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
    _interactive_mode.pop((user_id, thread_id or 0), None)


def get_interactive_msg_id(user_id: int, thread_id: int | None = None) -> int | None:
    """Get the interactive message ID for a user."""
    return _interactive_msgs.get((user_id, thread_id or 0))


def get_codex_prompt_state(
    user_id: int, thread_id: int | None = None
) -> CodexPromptState | None:
    """Get the pending Codex request_user_input state for a user/thread."""
    return _codex_prompt_states.get((user_id, thread_id or 0))


def has_codex_prompt(user_id: int, thread_id: int | None = None) -> bool:
    """Check whether a Codex prompt is currently active."""
    return get_codex_prompt_state(user_id, thread_id) is not None


def is_waiting_for_codex_notes_text(
    user_id: int,
    thread_id: int | None = None,
) -> bool:
    """Check whether the next user text should be saved as Codex notes."""
    state = get_codex_prompt_state(user_id, thread_id)
    return state.awaiting_notes_text if state else False


def _get_state_key(user_id: int, thread_id: int | None) -> tuple[int, int]:
    return (user_id, thread_id or 0)


def _current_codex_question(state: CodexPromptState) -> CodexPromptQuestion:
    return state.prompt.questions[state.question_index]


def _render_codex_prompt_text(state: CodexPromptState) -> str:
    """Render the current Codex prompt question as plain text."""
    question = _current_codex_question(state)
    total = len(state.prompt.questions)
    lines = [f"Question {state.question_index + 1}/{total}"]
    if question.header:
        lines.extend(["", question.header])
    lines.extend(["", question.question, ""])
    for index, option in enumerate(question.options, start=1):
        lines.append(f"{index}. {option.label}")
        if option.description:
            lines.append(f"   {option.description}")

    lines.append(f"{len(question.options) + 1}. None of the above")
    lines.append("   Optionally, add details in notes.")

    note_text = state.notes.get(question.question_id, "").strip()
    if note_text:
        lines.extend(["", f"Notes: {note_text}"])

    if state.awaiting_notes_text:
        lines.extend(
            [
                "",
                "Send your next text message in this topic to add notes for this question.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Tap an option below to submit this question.",
                "Optional: tap Add notes first if you want to include free text.",
            ]
        )
    return "\n".join(lines)


def _build_codex_prompt_keyboard(
    state: CodexPromptState,
) -> InlineKeyboardMarkup:
    """Build the inline keyboard for the current Codex prompt question."""
    question = _current_codex_question(state)
    rows: list[list[InlineKeyboardButton]] = []
    for option_index, option in enumerate(question.options):
        rows.append(
            [
                InlineKeyboardButton(
                    option.label,
                    callback_data=(
                        f"{CB_CODEX_PROMPT_OPTION}{state.question_index}:{option_index}:"
                        f"{state.window_id}"
                    )[:64],
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "None of the above",
                callback_data=(
                    f"{CB_CODEX_PROMPT_OPTION}{state.question_index}:{len(question.options)}:"
                    f"{state.window_id}"
                )[:64],
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "Update notes" if state.notes.get(question.question_id) else "Add notes",
                callback_data=(
                    f"{CB_CODEX_PROMPT_OTHER}{state.question_index}:{state.window_id}"
                )[:64],
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "Cancel",
                callback_data=f"{CB_CODEX_PROMPT_CANCEL}{state.window_id}"[:64],
            ),
            InlineKeyboardButton(
                "Refresh",
                callback_data=f"{CB_CODEX_PROMPT_REFRESH}{state.window_id}"[:64],
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _render_codex_prompt_message(
    bot: Bot,
    user_id: int,
    thread_id: int | None,
    state: CodexPromptState,
) -> bool:
    """Send or update the Telegram message for the current Codex prompt."""
    ikey = _get_state_key(user_id, thread_id)
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    text = _render_codex_prompt_text(state)
    keyboard = _build_codex_prompt_keyboard(state)
    thread_kwargs: dict[str, int] = {}
    if thread_id is not None:
        thread_kwargs["message_thread_id"] = thread_id

    existing_msg_id = _interactive_msgs.get(ikey)
    if existing_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=keyboard,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _interactive_mode[ikey] = state.window_id
            return True
        except Exception:
            logger.debug(
                "Edit failed for Codex prompt msg %s, sending new", existing_msg_id
            )
            _interactive_msgs.pop(ikey, None)

    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            link_preview_options=NO_LINK_PREVIEW,
            **thread_kwargs,  # type: ignore[arg-type]
        )
    except Exception as exc:
        logger.error("Failed to send Codex prompt UI: %s", exc)
        return False

    _interactive_msgs[ikey] = sent.message_id
    _interactive_mode[ikey] = state.window_id
    return True


async def _send_special_key(window_id: str, key: str) -> bool:
    return await tmux_manager.send_keys(window_id, key, enter=False, literal=False)


async def _move_codex_prompt_cursor_to_option(
    window_id: str,
    question: CodexPromptQuestion,
    option_index: int,
) -> bool:
    """Move the Codex TUI cursor to a target option within the current question."""
    pane_text = await tmux_manager.capture_pane(window_id)
    current_index = _find_codex_prompt_focus_index(pane_text, question)
    if current_index is None:
        current_index = 0

    if current_index == option_index:
        return True

    key = "Down" if option_index > current_index else "Up"
    steps = abs(option_index - current_index)
    for _ in range(steps):
        if not await _send_special_key(window_id, key):
            return False
        await asyncio.sleep(0.05)
    return True


def _find_codex_prompt_focus_index(
    pane_text: str | None,
    question: CodexPromptQuestion,
) -> int | None:
    """Return the currently highlighted Codex option index from pane text."""
    if not pane_text:
        return None

    current_question = False
    for line in pane_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if question.question in stripped:
            current_question = True
            continue
        if not current_question:
            continue
        match = _RE_CODEX_PROMPT_FOCUS.match(line)
        if match:
            focus_index = int(match.group(1)) - 1
            if 0 <= focus_index <= len(question.options):
                return focus_index
        if stripped.startswith("tab to add notes") or stripped.startswith(
            "tab or esc to clear notes"
        ):
            break
    return None


async def _select_codex_prompt_option(
    state: CodexPromptState,
    option_index: int,
) -> tuple[bool, bool]:
    """Apply the current answer to the tmux Codex TUI and advance state."""
    question = _current_codex_question(state)
    if option_index < 0 or option_index > len(question.options):
        return False, False

    if not await _move_codex_prompt_cursor_to_option(
        state.window_id, question, option_index
    ):
        return False, False

    notes_text = state.notes.get(question.question_id, "").strip()
    if notes_text:
        if not await _send_special_key(state.window_id, "Tab"):
            return False, False
        await asyncio.sleep(0.2)
        if not await tmux_manager.send_keys(
            state.window_id,
            notes_text,
            enter=False,
            literal=True,
        ):
            return False, False
        await asyncio.sleep(0.2)

    if not await _send_special_key(state.window_id, "Enter"):
        return False, False

    is_last = state.question_index >= len(state.prompt.questions) - 1
    if not is_last:
        state.question_index += 1
    state.awaiting_notes_text = False
    return True, is_last


async def handle_codex_prompt(
    bot: Bot,
    user_id: int,
    window_id: str,
    prompt: CodexPromptPayload,
    tool_use_id: str,
    thread_id: int | None = None,
) -> bool:
    """Show or refresh a native Telegram panel for Codex request_user_input."""
    ikey = _get_state_key(user_id, thread_id)
    state = _codex_prompt_states.get(ikey)
    if state and state.tool_use_id == tool_use_id:
        state.prompt = prompt
        state.window_id = window_id
    else:
        state = CodexPromptState(
            window_id=window_id,
            tool_use_id=tool_use_id,
            prompt=prompt,
        )
        _codex_prompt_states[ikey] = state
    return await _render_codex_prompt_message(bot, user_id, thread_id, state)


async def advance_codex_prompt_with_option(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None,
    question_index: int,
    option_index: int,
) -> tuple[bool, str]:
    """Record a Codex prompt option choice and advance the Telegram panel."""
    state = get_codex_prompt_state(user_id, thread_id)
    if state is None or state.window_id != window_id:
        return False, "Prompt is no longer active"
    if question_index != state.question_index:
        return False, "Prompt is out of date"

    question = _current_codex_question(state)
    if option_index < 0 or option_index > len(question.options):
        return False, "Invalid option"

    if option_index == len(question.options):
        answer_label = "None of the above"
    else:
        answer_label = question.options[option_index].label
    state.answers[question.question_id] = answer_label

    ok, completed = await _select_codex_prompt_option(state, option_index)
    if not ok:
        return False, "Failed to submit answer to Codex"

    if completed:
        await clear_interactive_msg(user_id, bot, thread_id)
        return True, "Submitted"

    rendered = await _render_codex_prompt_message(bot, user_id, thread_id, state)
    if not rendered:
        return False, "Failed to update prompt"
    return True, "Saved"


async def arm_codex_prompt_notes_text(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None,
    question_index: int,
) -> tuple[bool, str]:
    """Switch the active Codex prompt into note capture mode."""
    state = get_codex_prompt_state(user_id, thread_id)
    if state is None or state.window_id != window_id:
        return False, "Prompt is no longer active"
    if question_index != state.question_index:
        return False, "Prompt is out of date"

    state.awaiting_notes_text = True
    rendered = await _render_codex_prompt_message(bot, user_id, thread_id, state)
    if not rendered:
        return False, "Failed to update prompt"
    return True, "Send notes"


async def submit_codex_prompt_notes_text(
    bot: Bot,
    user_id: int,
    thread_id: int | None,
    text: str,
) -> tuple[bool, str]:
    """Use the next user text message as the notes for the current Codex question."""
    state = get_codex_prompt_state(user_id, thread_id)
    if state is None or not state.awaiting_notes_text:
        return False, "No pending notes prompt"

    notes_text = text.strip()
    if not notes_text:
        return False, "Notes cannot be empty"

    question = _current_codex_question(state)
    state.notes[question.question_id] = notes_text
    state.awaiting_notes_text = False

    rendered = await _render_codex_prompt_message(bot, user_id, thread_id, state)
    if not rendered:
        return False, "Failed to update prompt"
    return True, "Notes saved"


def _build_interactive_keyboard(
    window_id: str,
    ui_name: str = "",
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
    # Row 2: action keys
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

    # Build message with navigation keyboard
    keyboard = _build_interactive_keyboard(window_id, ui_name=content.name)

    # Send as plain text (no markdown conversion)
    text = content.content

    # Build thread kwargs for send_message
    thread_kwargs: dict[str, int] = {}
    if thread_id is not None:
        thread_kwargs["message_thread_id"] = thread_id

    # Check if we have an existing interactive message to edit
    existing_msg_id = _interactive_msgs.get(ikey)
    if existing_msg_id:
        if _interactive_text_cache.get(ikey) == text:
            _interactive_mode[ikey] = window_id
            return True
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=keyboard,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _interactive_mode[ikey] = window_id
            _interactive_text_cache[ikey] = text
            return True
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
        _interactive_text_cache[ikey] = text
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
    _interactive_text_cache.pop(ikey, None)
    _codex_prompt_states.pop(ikey, None)
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
