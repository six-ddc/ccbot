"""UI component builders for inline keyboards and directory browser.

Core responsibilities:
  - Build inline keyboards for session lists, history pagination, directory browser
  - Format directory browser UI with pagination
  - Build interactive UI keyboards (arrows, ESC, Enter)
  - Session detail formatting

Key functions: build_directory_browser(), build_history_keyboard(), 
build_interactive_keyboard(), build_session_detail(), build_list_keyboard()
"""

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .session import session_manager

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


def build_history_keyboard(
    window_name: str,
    page_index: int,
    total_pages: int,
    start_byte: int = 0,
    end_byte: int = 0,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for history pagination.

    Callback format: hp:<page>:<window>:<start>:<end> or hn:<page>:<window>:<start>:<end>
    When start=0 and end=0, it means full history (no byte range filter).
    """
    if total_pages <= 1:
        return None

    buttons = []
    if page_index > 0:
        cb_data = f"{CB_HISTORY_PREV}{page_index - 1}:{window_name}:{start_byte}:{end_byte}"
        buttons.append(InlineKeyboardButton(
            "◀ Older",
            callback_data=cb_data[:64],
        ))

    buttons.append(InlineKeyboardButton(f"{page_index + 1}/{total_pages}", callback_data="noop"))

    if page_index < total_pages - 1:
        cb_data = f"{CB_HISTORY_NEXT}{page_index + 1}:{window_name}:{start_byte}:{end_byte}"
        buttons.append(InlineKeyboardButton(
            "Newer ▶",
            callback_data=cb_data[:64],
        ))

    return InlineKeyboardMarkup([buttons])


def build_directory_browser(current_path: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    """Build directory browser UI.

    Returns: (text, keyboard, subdirs) where subdirs is the full list for caching.
    """
    path = Path(current_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        path = Path.cwd()

    try:
        subdirs = sorted([
            d.name for d in path.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        ])
    except (PermissionError, OSError):
        subdirs = []

    total_pages = max(1, (len(subdirs) + DIRS_PER_PAGE - 1) // DIRS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DIRS_PER_PAGE
    page_dirs = subdirs[start:start + DIRS_PER_PAGE]

    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(page_dirs), 2):
        row = []
        for j, name in enumerate(page_dirs[i:i+2]):
            display = name[:12] + "…" if len(name) > 13 else name
            # Use global index (start + i + j) to avoid long dir names in callback_data
            idx = start + i + j
            row.append(InlineKeyboardButton(f"📁 {display}", callback_data=f"{CB_DIR_SELECT}{idx}"))
        buttons.append(row)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀", callback_data=f"{CB_DIR_PAGE}{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶", callback_data=f"{CB_DIR_PAGE}{page+1}"))
        buttons.append(nav)

    action_row: list[InlineKeyboardButton] = []
    # Allow going up unless at filesystem root
    if path != path.parent:
        action_row.append(InlineKeyboardButton("..", callback_data=CB_DIR_UP))
    action_row.append(InlineKeyboardButton("Select", callback_data=CB_DIR_CONFIRM))
    action_row.append(InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL))
    buttons.append(action_row)

    display_path = str(path).replace(str(Path.home()), "~")
    if not subdirs:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\n_(No subdirectories)_"
    else:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\nTap a folder to enter, or select current directory"

    return text, InlineKeyboardMarkup(buttons), subdirs


async def build_session_detail(
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


def build_interactive_keyboard(
    window_name: str, ui_name: str = "",
) -> InlineKeyboardMarkup:
    """Build keyboard for interactive UI navigation.

    ``ui_name`` controls the layout: ``RestoreCheckpoint`` omits ←/→ keys
    since only vertical selection is needed.
    """
    vertical_only = ui_name == "RestoreCheckpoint"

    rows: list[list[InlineKeyboardButton]] = []
    # Row 1: directional keys
    rows.append([
        InlineKeyboardButton("↑", callback_data=f"{CB_ASK_UP}{window_name}"[:64]),
    ])
    if vertical_only:
        rows.append([
            InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{window_name}"[:64]),
        ])
    else:
        rows.append([
            InlineKeyboardButton("←", callback_data=f"{CB_ASK_LEFT}{window_name}"[:64]),
            InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{window_name}"[:64]),
            InlineKeyboardButton("→", callback_data=f"{CB_ASK_RIGHT}{window_name}"[:64]),
        ])
    # Row 2: action keys
    rows.append([
        InlineKeyboardButton("⎋ Esc", callback_data=f"{CB_ASK_ESC}{window_name}"[:64]),
        InlineKeyboardButton("🔄", callback_data=f"{CB_ASK_REFRESH}{window_name}"[:64]),
        InlineKeyboardButton("⏎ Enter", callback_data=f"{CB_ASK_ENTER}{window_name}"[:64]),
    ])
    return InlineKeyboardMarkup(rows)


async def build_list_keyboard(
    windows: list[str],
) -> InlineKeyboardMarkup:
    """Build keyboard for session list."""
    buttons: list[list[InlineKeyboardButton]] = []

    # Add window buttons (2 per row for better mobile UX)
    for i in range(0, len(windows), 2):
        row = []
        for j in range(2):
            if i + j < len(windows):
                wname = windows[i + j]
                display = wname[:12] + "…" if len(wname) > 13 else wname
                row.append(InlineKeyboardButton(
                    f"📌 {display}",
                    callback_data=f"{CB_LIST_SELECT}{wname}"[:64],
                ))
        buttons.append(row)

    # Add "New Session" button
    buttons.append([InlineKeyboardButton("➕ New Session", callback_data=CB_LIST_NEW)])

    return InlineKeyboardMarkup(buttons)
