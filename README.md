# CCMux

Telegram Bot for monitoring and interacting with Claude Code sessions.

## Features

- **Monitor Claude Code sessions** - Automatically detects sessions from `~/.claude/projects/` that have active tmux windows
- **Real-time notifications** - Get Telegram notifications when Claude responds (for your active window)
- **Send messages** - Forward text to Claude Code via tmux keystrokes
- **Create new sessions** - Start new Claude Code sessions directly from Telegram
- **Message history** - Browse conversation history with pagination (◀ Older / Newer ▶)
- **Persistent state** - Active window selection survives restarts

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                       Telegram Bot                            │
│  - Browse Claude sessions (only those with tmux windows)      │
│  - Select active window for sending                           │
│  - Send text messages to Claude Code                          │
│  - View message history with pagination                       │
│  - Create new sessions (tmux window + claude command)         │
└───────────────────────────────────────────────────────────────┘
         │                                    │
         │ Monitor (polling JSONL)            │ Send (tmux keys)
         ▼                                    ▼
┌─────────────────────┐           ┌─────────────────────┐
│  Claude Sessions    │◄─────────►│    Tmux Windows     │
│  ~/.claude/projects │  matched  │    (by cwd)         │
│  - sessions-index   │   by      │                     │
│  - *.jsonl files    │ projectPath│  claude running in │
└─────────────────────┘           │  each window        │
                                  └─────────────────────┘
```

**Key design decisions:**
- **State anchored to tmux window names** — `state.json` stores `{user_id: window_name}`. Window names are stable; cwd (project path) is resolved dynamically from tmux at runtime.
- Only sessions with matching tmux windows are displayed (enables bidirectional communication)
- Sessions are matched by comparing `projectPath` from Claude session index with tmux window's current working directory
- New sessions are created by opening a tmux window and running `claude` command
- Notifications are sent only to users whose active window's cwd matches the message's project path

## Installation

```bash
# Clone and enter directory
cd ccmux

# Install dependencies with uv
uv sync
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Required environment variables:

- `TELEGRAM_BOT_TOKEN` - Your Telegram Bot token from @BotFather
- `ALLOWED_USERS` - Comma-separated list of allowed Telegram user IDs

Optional:

- `TMUX_SESSION_NAME` - Tmux session name (default: `ccmux`)
- `MONITOR_POLL_INTERVAL` - Polling interval in seconds (default: `2.0`)
- `MONITOR_STABLE_WAIT` - Wait time for file stability (default: `2.0`)

## Usage

Start the bot:

```bash
uv run ccmux
```

### Telegram Interface

Use `/start` to see Claude Code sessions running in tmux:

```
🤖 Claude Code Monitor

📊 3 sessions in tmux
📤 Active: [ccmux]

Tap a session to select it.
Send text to forward to active session.
```

**Bottom Menu (Persistent Keyboard):**

```
┌─────────────────────────────────────────────┐
│ 📤 [ccmux] CCMux Telegram Bot...            │  ← Row 1: Session
│ [resume] Resume Builder Project...          │  ← Row 2: Session
│ [tickflow] Task Management System...        │  ← Row 3: Session
│   ⬅️    1/2    ➡️           ➕ New          │  ← Row 4: Nav + New
└─────────────────────────────────────────────┘
```

- **Rows 1-3**: Session buttons (one per row, max 3 per page)
- **Row 4**: Navigation buttons (if more than 3 sessions) + New session button

**Session Icons:**
- 📤 Active for sending (your messages go here)

**Note:** Only sessions with active tmux terminals are shown. Claude Code sessions outside tmux are not managed.

**Actions:**
1. **Tap a session** - Select it as active; shows recent 5 messages
2. **Browse history** - Use ◀ Older / Newer ▶ inline buttons to page through messages
3. **Send text** - Any message goes to your active session via tmux
4. **➕ New** - Create a new Claude Code session in a specified directory

### Message History

When you select a session, the bot shows the most recent 5 messages:

```
📋 [project-name] Messages (6-10 of 42)

👤 fix the login bug

🤖 I'll look into the login bug...

👤 also check the session timeout

🤖 Found the issue - the session...

👤 great, deploy it

[◀ Older]    [2/9]    [Newer ▶]
```

- Messages are displayed with 👤 (user) and 🤖 (assistant) icons
- Use inline buttons to navigate through history pages
- Messages are edited in-place (no message spam)

### Commands

- `/start` - Browse sessions and manage active window
- `/cancel` - Cancel current operation (e.g., directory browser)

### Sending Messages

1. Select a session (tap it in the bottom menu)
2. The session will show 📤 icon when active
3. Send any text message - it will be forwarded to Claude Code via tmux

### Creating New Sessions

1. Tap **➕ New** in the bottom menu
2. Browse and select a directory using the inline directory browser
3. A new tmux window will be created and `claude` command will start automatically

The new session will appear in the bottom menu once Claude Code initializes.

## Running Claude Code in tmux

For the bot to send messages, Claude Code must be running in a tmux window.

### Option 1: Create via Telegram Bot (Recommended)

1. Start the bot with `/start`
2. Tap **➕ New** in the bottom menu
3. Select the project directory
4. The bot creates a tmux window and starts `claude` automatically

### Option 2: Create Manually

```bash
# Attach to the ccmux tmux session
tmux attach -t ccmux

# Create a new window and navigate to your project
tmux new-window -n cc:myproject
cd ~/Code/myproject
claude

# Detach with Ctrl+b d
```

**Note:** Window names must start with the configured prefix (default `cc:`) to be recognized by the bot. The bot automatically creates/uses a tmux session named `ccmux` (configurable via `TMUX_SESSION_NAME`).

## Data Storage

- `~/.ccmux/state.json` - Active window selections (`{user_id: window_name}`)
- `~/.ccmux/monitor_state.json` - Session monitoring state (prevents duplicate notifications)
- `~/.claude/projects/` - Claude Code session data (read-only)

## How It Works

1. **Session Discovery**: Scans `~/.claude/projects/*/sessions-index.json` to find all Claude sessions
2. **Window Matching**: Matches sessions to tmux windows by comparing `projectPath` with window cwd
3. **Monitoring**: Polls session JSONL files for new assistant messages
4. **Notifications**: When a new message is detected, resolves each user's active `window_name → cwd`, notifies if cwd matches
5. **Sending**: Sends keystrokes to the user's active tmux window
6. **History**: Reads JSONL transcript files, extracts user/assistant messages, displays with pagination

## File Structure

```
src/ccmux/
├── main.py              # Entry point (tmux session init + bot start)
├── config.py            # Configuration from environment
├── bot.py               # Telegram bot handlers (menu, callbacks, history)
├── session.py           # Session management + message history
├── session_monitor.py   # Session file monitoring (polling JSONL)
├── monitor_state.py     # Monitor state persistence
├── transcript_parser.py # JSONL parsing for Claude sessions
├── telegram_sender.py   # Message sending utilities
└── tmux_manager.py      # Tmux window management (list, find, send, create)
```
