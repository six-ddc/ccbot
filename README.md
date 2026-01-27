# CCMux

Telegram Bot for monitoring and interacting with Claude Code sessions running in tmux.

## Features

- **Monitor Claude Code sessions** — Auto-detects sessions from `~/.claude/projects/` with active tmux windows
- **Real-time notifications** — Get Telegram messages when Claude responds (text and thinking content)
- **Local command output** — See stdout from local commands (e.g. `git status`) in Telegram
- **Send messages** — Forward text to Claude Code via tmux keystrokes
- **Slash command forwarding** — Send any `/command` directly to Claude Code (e.g. `/clear`, `/compact`, `/cost`)
- **Create new sessions** — Start Claude Code sessions from Telegram via directory browser
- **Kill sessions** — Terminate sessions remotely
- **Message history** — Browse conversation history with pagination
- **Persistent state** — Active window selection survives restarts

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                       Telegram Bot                            │
│  - /list: Browse sessions (inline buttons)                   │
│  - Select active window for sending                          │
│  - Send text messages to Claude Code                         │
│  - Forward /commands to Claude Code                          │
│  - View message history with pagination                      │
│  - Create / kill sessions                                    │
└───────────────────────────────────────────────────────────────┘
         │                                    │
         │ Monitor (polling JSONL)            │ Send (tmux keys)
         ▼                                    ▼
┌─────────────────────┐           ┌─────────────────────┐
│  Claude Sessions    │◄─────────►│    Tmux Windows     │
│  ~/.claude/projects │  matched  │    (by cwd)         │
│  - sessions-index   │   by      │                     │
│  - *.jsonl files    │ session_id│  claude running in │
└─────────────────────┘           │  each window        │
                                  └─────────────────────┘
```

**Key design decisions:**
- **State anchored to tmux window names** — `state.json` stores `{user_id: window_name}` and `{window_name: window_state}`. Window names are stable.
- **Persistent session association** — Each window stores its associated `session_id`, `last_msg_id`, and `pending_text` for session detection.
- **New session detection** — When a new session is created or after `/clear`, the session is detected by matching the user's first message against recent JSONL files.
- **Message ID tracking** — `last_msg_id` enables correct message polling after session switches.
- Only sessions with matching tmux windows are displayed (enables bidirectional communication)
- Notifications sent only to users whose active window matches the message's session

## Installation

```bash
cd ccmux
uv sync
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

**Required:**

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ALLOWED_USERS` | Comma-separated Telegram user IDs |

**Optional:**

| Variable | Default | Description |
|---|---|---|
| `TMUX_SESSION_NAME` | `ccmux` | Tmux session name |
| `CLAUDE_COMMAND` | `claude --dangerously-skip-permissions` | Command to run in new windows |
| `BROWSE_ROOT_DIR` | cwd | Root directory for file browser |
| `MONITOR_POLL_INTERVAL` | `2.0` | Polling interval in seconds |
| `MONITOR_STABLE_WAIT` | `2.0` | File stability wait time in seconds |

## Usage

```bash
uv run ccmux
```

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/list` | Browse active sessions (inline buttons) |
| `/history` | Show history for active session |
| `/cancel` | Cancel current operation |
| `/clear` | Forward to Claude Code: clear conversation |
| `/compact` | Forward to Claude Code: compact context |
| `/cost` | Forward to Claude Code: show token usage |
| `/help` | Forward to Claude Code: show help |
| `/review` | Forward to Claude Code: code review |
| `/doctor` | Forward to Claude Code: diagnose environment |
| `/memory` | Forward to Claude Code: edit CLAUDE.md |
| `/init` | Forward to Claude Code: init project CLAUDE.md |

Any unrecognized `/command` is also forwarded to Claude Code as-is.

### Session List (`/list`)

Sessions are shown as inline buttons. Tap a session to select it as active:

```
📊 3 active sessions:

[✅ [ccmux] Telegram Bot...]
[   [resume] Resume Builder...]
[   [tickflow] Task Management...]
[➕ New Session]
```

After selecting a session, you get detail info and action buttons:

```
📤 Selected: ccmux

📝 Telegram Bot for Claude Code monitoring
💬 42 messages

[📋 History] [🔄 Refresh] [❌ Kill]
```

### Sending Messages

1. Use `/list` to select a session
2. Send any text — it gets forwarded to Claude Code via tmux keystrokes
3. The bot creates a ⏳ placeholder, then sends Claude's response when ready

### Message History

Navigate with inline buttons:

```
📋 [project-name] Messages (6-10 of 42)

👤 fix the login bug

🤖 I'll look into the login bug...

👤 also check the session timeout

🤖 Found the issue...

[◀ Older]    [2/9]    [Newer ▶]
```

### Creating New Sessions

1. Tap **➕ New Session** in `/list`
2. Browse and select a directory using the inline directory browser
3. A new tmux window is created and `claude` starts automatically

### Notifications

The monitor polls session JSONL files every 2 seconds and sends notifications for:
- **Assistant responses** — Claude's text replies
- **Local command output** — stdout from commands like `git status`, prefixed with `❯ command_name`

Notifications are only sent to users whose active window matches the session.

## Running Claude Code in tmux

### Option 1: Create via Telegram (Recommended)

1. Run `/list`
2. Tap **➕ New Session**
3. Select the project directory

### Option 2: Create Manually

```bash
tmux attach -t ccmux
tmux new-window -n cc:myproject
cd ~/Code/myproject
claude
```

Window names must start with the prefix `cc:` to be recognized.

## Data Storage

| Path | Description |
|---|---|
| `~/.ccmux/state.json` | Active window selections and window states (`{user_id: window_name}`, `{window_name: {session_id, last_msg_id, pending_text}}`) |
| `~/.ccmux/monitor_state.json` | Monitor state (prevents duplicate notifications) |
| `~/.claude/projects/` | Claude Code session data (read-only) |

## File Structure

```
src/ccmux/
├── main.py              # Entry point (tmux session init + bot start)
├── config.py            # Configuration from environment variables
├── bot.py               # Telegram bot handlers and inline UI
├── session.py           # Session management + message history
├── session_monitor.py   # JSONL file monitoring (polling + change detection)
├── monitor_state.py     # Monitor state persistence
├── transcript_parser.py # Claude Code JSONL transcript parsing
├── telegram_sender.py   # Message splitting and sending utilities
└── tmux_manager.py      # Tmux window management (list, create, send keys, kill)
```
