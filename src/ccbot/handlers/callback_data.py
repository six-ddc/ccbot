"""Callback data constants for Telegram inline keyboards.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Constants:
  - CB_HISTORY_*: History pagination
  - CB_DIR_*: Directory browser navigation
  - CB_WIN_*: Window picker (bind existing unbound window)
  - CB_SCREENSHOT_*: Screenshot refresh
  - CB_ASK_*: Interactive UI navigation (arrows, enter, esc)
  - CB_SESSIONS_*: Sessions dashboard (refresh, new, kill)
  - CB_STATUS_*: Status message action buttons (esc, screenshot)
  - CB_RECOVERY_*: Dead window recovery UI (fresh, continue, resume)
  - CB_KEYS_PREFIX: Screenshot control keys (kb:<key_id>:<window>)
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

# Window picker (bind existing unbound window)
CB_WIN_BIND = "wb:sel:"  # wb:sel:<index>
CB_WIN_NEW = "wb:new"  # proceed to directory browser
CB_WIN_CANCEL = "wb:cancel"

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Interactive UI (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"  # aq:up:<window>
CB_ASK_DOWN = "aq:down:"  # aq:down:<window>
CB_ASK_LEFT = "aq:left:"  # aq:left:<window>
CB_ASK_RIGHT = "aq:right:"  # aq:right:<window>
CB_ASK_ESC = "aq:esc:"  # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:"  # aq:enter:<window>
CB_ASK_SPACE = "aq:spc:"  # aq:spc:<window>
CB_ASK_TAB = "aq:tab:"  # aq:tab:<window>
CB_ASK_REFRESH = "aq:ref:"  # aq:ref:<window>

# Sessions dashboard
CB_SESSIONS_REFRESH = "sess:ref"
CB_SESSIONS_NEW = "sess:new"
CB_SESSIONS_KILL = "sess:kill:"  # sess:kill:<window_id>
CB_SESSIONS_KILL_CONFIRM = "sess:killok:"  # sess:killok:<window_id>

# Status message action buttons
CB_STATUS_ESC = "st:esc:"  # st:esc:<window_id>
CB_STATUS_SCREENSHOT = "st:ss:"  # st:ss:<window_id>

# Recovery UI (dead window)
CB_RECOVERY_FRESH = "rec:f:"  # rec:f:<window_id>
CB_RECOVERY_CONTINUE = "rec:c:"  # rec:c:<window_id>
CB_RECOVERY_RESUME = "rec:r:"  # rec:r:<window_id>
CB_RECOVERY_PICK = "rec:p:"  # rec:p:<index> (resume picker selection)
CB_RECOVERY_CANCEL = "rec:x"  # cancel recovery

# Resume command (browse all sessions)
CB_RESUME_PICK = "res:p:"  # res:p:<index> (session selection)
CB_RESUME_PAGE = "res:pg:"  # res:pg:<page> (pagination)
CB_RESUME_CANCEL = "res:x"  # cancel resume browser

# Screenshot control keys
CB_KEYS_PREFIX = "kb:"  # kb:<key_id>:<window>
