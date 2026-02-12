# CCBot

[![CI](https://github.com/alexei-led/ccbot/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/alexei-led/ccbot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ccbot)](https://pypi.org/project/ccbot/)
[![Python](https://img.shields.io/pypi/pyversions/ccbot)](https://pypi.org/project/ccbot/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Control [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions from your phone. CCBot bridges Telegram to tmux — monitor output, respond to prompts, and manage sessions without touching your computer.

## Why CCBot?

Claude Code runs in your terminal. When you step away — commuting, on the couch, or just away from your desk — the session keeps working, but you lose visibility and control.

CCBot fixes this. The key insight: it operates on **tmux**, not the Claude Code SDK. Your Claude Code process stays exactly where it is, in a tmux window on your machine. CCBot reads its output and sends keystrokes to it. This means:

- **Desktop to phone, mid-conversation** — Claude is working on a refactor? Walk away and keep monitoring from Telegram
- **Phone back to desktop, anytime** — `tmux attach` and you're back in the terminal with full scrollback
- **Multiple sessions in parallel** — Each Telegram topic maps to a separate tmux window

Other Telegram bots for Claude Code wrap the SDK to create isolated API sessions that can't be resumed in your terminal. CCBot is different — it's a thin control layer over tmux, so the terminal remains the source of truth.

## How It Works

```
Telegram Group                  Your Machine
┌──────────────┐               ┌──────────────────────┐
│ Topic: api   │──── text ────▶│ tmux window @0       │
│              │◀─ responses ──│  └─ claude (running)  │
├──────────────┤               ├──────────────────────┤
│ Topic: ui    │──── text ────▶│ tmux window @1       │
│              │◀─ responses ──│  └─ claude (running)  │
└──────────────┘               └──────────────────────┘
  1 topic = 1 window = 1 session
```

Each Telegram Forum topic binds to one tmux window running one Claude Code instance. Messages you type in the topic are sent as keystrokes to the tmux pane; Claude's output is parsed from session transcripts and delivered back as Telegram messages.

## Features

**Session control**

- Send messages and `/commands` directly to Claude Code (`/clear`, `/compact`, `/cost`, etc.)
- Interactive prompts (AskUserQuestion, ExitPlanMode, Permission) rendered as inline keyboards
- Terminal screenshots — capture the current pane as a PNG image
- Sessions dashboard (`/sessions`) — overview of all sessions with status and kill buttons

**Real-time monitoring**

- Assistant responses, thinking content, tool use/result pairs, and command output
- Live status line with spinner text (what Claude is currently doing)
- MarkdownV2 formatting with automatic plain text fallback

**Session management**

- Directory browser for creating new sessions from Telegram
- Auto-sync: create a tmux window manually and the bot auto-creates a matching topic
- Fresh/Continue/Resume recovery when a session dies
- Message history with paginated browsing (`/history`)
- Persistent state — bindings and read offsets survive restarts

**Extensibility**

- Auto-discovers Claude Code skills and custom commands into the Telegram menu
- Multi-instance support — run separate bots per Telegram group on the same machine
- Configurable via environment variables

## Quick Start

### Prerequisites

- **Python 3.14+**
- **tmux** — installed and in PATH
- **Claude Code** — the `claude` CLI installed and authenticated

### Install

```bash
# macOS (Homebrew)
brew install alexei-led/tap/ccbot

# PyPI
uv tool install ccbot

# Or with pipx
pipx install ccbot
```

### Configure

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather)
2. Enable **Topics** in your bot (BotFather > Bot Settings > Groups > Topics in Groups > Enable)
3. Add the bot to a Telegram group that has Topics enabled
4. Create `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

> Get your user ID from [@userinfobot](https://t.me/userinfobot) on Telegram.

### Install the session hook

```bash
ccbot hook --install
```

This registers a Claude Code `SessionStart` hook so the bot can auto-track which session runs in each tmux window.

### Run

```bash
ccbot
```

Open your Telegram group, create a new topic, send a message — a directory browser appears. Pick a project directory and you're connected to Claude Code.

## Configuration

| Variable                | Default        | Description                       |
| ----------------------- | -------------- | --------------------------------- |
| `TELEGRAM_BOT_TOKEN`    | _(required)_   | Bot token from @BotFather         |
| `ALLOWED_USERS`         | _(required)_   | Comma-separated Telegram user IDs |
| `CCBOT_DIR`             | `~/.ccbot`     | Config and state directory        |
| `TMUX_SESSION_NAME`     | `ccbot`        | tmux session name                 |
| `CLAUDE_COMMAND`        | `claude`       | Command to launch Claude Code     |
| `CCBOT_GROUP_ID`        | _(all groups)_ | Restrict to one Telegram group    |
| `CCBOT_INSTANCE_NAME`   | hostname       | Display label for this instance   |
| `MONITOR_POLL_INTERVAL` | `2.0`          | Seconds between transcript polls  |

## Documentation

See **[docs/guides.md](docs/guides.md)** for advanced topics: multi-instance setup, manual tmux usage, data storage, and environment variables.

## Credits

CCBot is a maintained fork of [ccbot](https://github.com/six-ddc/ccbot) by [six-ddc](https://github.com/six-ddc). See [FORK.md](FORK.md) for the fork history and divergences.

## License

[MIT](LICENSE)
