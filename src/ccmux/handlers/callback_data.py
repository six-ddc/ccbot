"""Callback data constants for Telegram inline keyboards.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Constants:
  - CB_HISTORY_*: History pagination
  - CB_DIR_*: Directory browser navigation
  - CB_SESSION_*: Session actions (history, refresh, kill)
  - CB_SCREENSHOT_*: Screenshot refresh
  - CB_ASK_*: Interactive UI navigation (arrows, enter, esc)
  - CB_LIST_*: Session list selection
"""

# History pagination
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer

# Directory browser
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"

# Session actions
CB_SESSION_HISTORY = "sa:hist:"
CB_SESSION_REFRESH = "sa:ref:"
CB_SESSION_KILL = "sa:kill:"

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Interactive UI (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"       # aq:up:<window>
CB_ASK_DOWN = "aq:down:"   # aq:down:<window>
CB_ASK_LEFT = "aq:left:"   # aq:left:<window>
CB_ASK_RIGHT = "aq:right:" # aq:right:<window>
CB_ASK_ESC = "aq:esc:"     # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:" # aq:enter:<window>
CB_ASK_REFRESH = "aq:ref:" # aq:ref:<window>

# Session list
CB_LIST_SELECT = "ls:sel:"
CB_LIST_NEW = "ls:new"
