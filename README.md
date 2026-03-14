# CCBot

[中文文档](README_CN.md)  
[Русская документация](README_RU.md)

Control Claude Code or Codex sessions from Telegram while they keep running in tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## What It Is

CCBot is a thin Telegram control layer on top of tmux.

- Your Claude Code or Codex process keeps running in a tmux window.
- Telegram lets you watch output, reply, and send commands remotely.
- You can always switch back to the terminal with `tmux attach`.

Each Telegram topic maps to one tmux window and one Claude Code or Codex session.

## Features

- One Telegram topic per session
- Real-time replies and status updates
- Resume old sessions from Telegram
- Voice message transcription
- Inline UI for prompts and selections
- Message history and screenshots
- Works with Claude Code or Codex

## Prerequisites

- `tmux`
- `claude` or `codex`
- A Telegram bot created with [@BotFather](https://t.me/BotFather)

## Install

### Install from GitHub

```bash
uv tool install git+https://github.com/six-ddc/ccmux.git
```

Or:

```bash
pipx install git+https://github.com/six-ddc/ccmux.git
```

### Install from source

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

## Basic Config

**1. Create a Telegram bot and enable Threaded Mode:**

1. Chat with [@BotFather](https://t.me/BotFather) to create a new bot and get your bot token
2. Open @BotFather's profile page, tap **Open App** to launch the mini app
3. Select your bot, then go to **Settings** > **Bot Settings**
4. Enable **Threaded Mode**

**2. Configure environment variables:**

Create `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

You can also keep a more complete manual config like this:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=123456789

# Default runtime for new bot processes: claude or codex
CCBOT_RUNTIME=claude

# Commands used when opening new tmux windows
CLAUDE_COMMAND=claude
CODEX_COMMAND=codex --no-alt-screen

# Optional
TMUX_SESSION_NAME=ccbot
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
```

Useful optional settings:

| Variable | Default | Description |
| --- | --- | --- |
| `CCBOT_RUNTIME` | `claude` | Which CLI to start in new windows: `claude` or `codex` |
| `CCBOT_DIR` | `~/.ccbot` | Config and state directory |
| `CLAUDE_COMMAND` | `claude` | Claude Code command |
| `CODEX_COMMAND` | `codex --no-alt-screen` | Codex command |
| `CODEX_HOME` | `~/.codex` | Codex config/session directory |
| `TMUX_SESSION_NAME` | `ccbot` | tmux session name |
| `MONITOR_POLL_INTERVAL` | `2.0` | Session monitor polling interval in seconds |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | Show hidden directories in the picker |
| `OPENAI_API_KEY` | _(empty)_ | Needed only for voice transcription |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Optional OpenAI-compatible base URL |

Notes:

- `ALLOWED_USERS` accepts one or more Telegram user IDs separated by commas.
- `ccbot --run claude|codex` only affects the current startup and does not rewrite your `.env`.
- If you installed from source, you can also keep a project-local `.env` for testing, but `~/.ccbot/.env` is the normal long-term setup.

If you run on a VPS with no interactive terminal for approvals, you may want a
less interactive Claude Code command, for example:

```bash
CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
```

## Hook Setup

Install hooks with:

```bash
ccbot hook --install
```

If you want to install both Claude Code and Codex hooks explicitly:

```bash
ccbot hook --install --run all
```

Codex hooks are still experimental in `v0.114.0`. If you start Codex manually
outside CCBot, enable them with:

```bash
codex --enable codex_hooks
```

If you prefer to configure hooks manually instead of using `ccbot hook --install`:

Claude Code `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccbot hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Codex `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccbot hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

This lets CCBot keep `session_map.json` updated so tmux windows stay linked to
the right Claude Code or Codex session.

## Start The Bot

```bash
ccbot
```

If installed from source:

```bash
uv run ccbot
```

To use Codex just for this bot process:

```bash
ccbot --run codex
```

## Daily Use

1. Create a new topic in your Telegram group.
2. Send any message in that topic.
3. Pick a project directory.
4. Resume an old session or start a new one.
5. Keep chatting in that topic.

Text and voice messages are forwarded to the linked Claude Code or Codex
session. Closing the topic closes the linked tmux window.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Show the welcome message |
| `/history` | Show message history for the current topic |
| `/screenshot` | Capture the terminal as an image |
| `/esc` | Send Escape to interrupt the current session |
| `/unbind` | Unbind this topic but keep the tmux window running |
| `/kill` | End the session for this topic |

Most other `/...` commands are forwarded directly to the current runtime.

- Claude Code menu includes commands such as `/usage`, `/help`, `/memory`, and `/model`.
- Codex menu includes commands such as `/status` and `/plan`.
- You can still type other runtime commands manually, for example `/clear`, `/compact`, `/review`, or `/model`.

## Manual tmux Use

If you want to create a window yourself:

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
claude
```

Or with Codex:

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
codex --no-alt-screen --enable codex_hooks --enable default_mode_request_user_input
```
