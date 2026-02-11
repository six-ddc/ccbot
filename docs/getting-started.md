# Getting Started

## Prerequisites

- **tmux** — must be installed and available in PATH
- **Claude Code** — the CLI tool (`claude`) must be installed

## Installation

### Option 1: Install from GitHub (Recommended)

```bash
# Using uv (recommended)
uv tool install git+https://github.com/six-ddc/ccmux.git

# Or using pipx
pipx install git+https://github.com/six-ddc/ccmux.git
```

### Option 2: Install from source

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

When running from source, use `uv run ccbot` instead of `ccbot`.

## Configuration

### 1. Create a Telegram bot

1. Chat with [@BotFather](https://t.me/BotFather) to create a new bot and get your bot token
2. Open @BotFather's profile page, tap **Open App** to launch the mini app
3. Select your bot, then go to **Settings** > **Bot Settings**
4. Enable **Threaded Mode**

### 2. Set environment variables

Create `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

**Required:**

| Variable             | Description                       |
| -------------------- | --------------------------------- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather         |
| `ALLOWED_USERS`      | Comma-separated Telegram user IDs |

**Optional:**

| Variable                | Default    | Description                                      |
| ----------------------- | ---------- | ------------------------------------------------ |
| `CCBOT_DIR`             | `~/.ccbot` | Config/state directory (`.env` loaded from here) |
| `TMUX_SESSION_NAME`     | `ccbot`    | Tmux session name                                |
| `CLAUDE_COMMAND`        | `claude`   | Command to run in new windows                    |
| `MONITOR_POLL_INTERVAL` | `2.0`      | Polling interval in seconds                      |
| `CCBOT_GROUP_ID`        | —          | Telegram group chat ID this instance owns        |
| `CCBOT_INSTANCE_NAME`   | hostname   | Display name for this instance                   |

> If running on a VPS with no interactive terminal to approve permissions:
>
> ```
> CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

### 3. Install the session hook

```bash
ccbot hook --install
```

Or manually add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ]
  }
}
```

The hook writes window-session mappings so the bot auto-tracks which Claude session runs in each tmux window — even after `/clear` or session restarts.

### 4. Run

```bash
ccbot
```

## Your First Session

**1 Topic = 1 Window = 1 Session.** The bot runs in Telegram Forum (topics) mode.

**Creating a session:**

1. Create a new topic in the Telegram group
2. Send any message in the topic
3. A directory browser appears — select the project directory
4. A tmux window is created, `claude` starts, and your pending message is forwarded

**Sending messages:**

Once a topic is bound to a session, just send text — it gets forwarded to Claude Code via tmux keystrokes.

**Killing a session:**

Close or delete the topic in Telegram. The associated tmux window is automatically killed and the binding is removed.

## Commands

**Bot commands:**

| Command     | Description                    |
| ----------- | ------------------------------ |
| `/new`      | Create new Claude session      |
| `/history`  | Message history for this topic |
| `/sessions` | Sessions dashboard             |

**Claude Code commands (forwarded via tmux):**

| Command    | Description                  |
| ---------- | ---------------------------- |
| `/clear`   | Clear conversation history   |
| `/compact` | Compact conversation context |
| `/cost`    | Show token/cost usage        |
| `/help`    | Show Claude Code help        |
| `/memory`  | Edit CLAUDE.md               |

Any unrecognized `/command` is forwarded to Claude Code as-is (e.g. `/review`, `/doctor`, `/init`). Skills and custom commands from `~/.claude/` are auto-discovered and added to the Telegram menu.

**Inline buttons** (appear on status messages):

| Button         | Action                          |
| -------------- | ------------------------------- |
| `[Esc]`        | Send Escape to interrupt Claude |
| `[Screenshot]` | Capture terminal screenshot     |

## Notifications

The monitor polls session transcripts and sends notifications for:

- **Assistant responses** — Claude's text replies
- **Thinking content** — Shown as expandable blockquotes
- **Tool use/result** — Summarized with stats (e.g. "Read 42 lines", "Found 5 matches")
- **Local command output** — stdout from commands like `git status`

## Message History

Browse past messages with inline pagination:

```
📋 [project-name] Messages (42 total)

───── 14:32 ─────

👤 fix the login bug

───── 14:33 ─────

I'll look into the login bug...

[◀ Older]    [2/9]    [Newer ▶]
```
