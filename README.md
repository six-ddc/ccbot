# CCBot

Control Claude Code sessions remotely via Telegram — monitor, interact, and manage AI coding sessions running in tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## Contents

- [Why CCBot?](#why-ccbot)
- [Features](#features)
- [Quick Start](#quick-start)
- [Documentation](#documentation)

## Why CCBot?

Claude Code runs in your terminal. When you step away from your computer — commuting, on the couch, or just away from your desk — the session keeps working, but you lose visibility and control.

CCBot solves this by letting you **seamlessly continue the same session from Telegram**. The key insight is that it operates on **tmux**, not the Claude Code SDK. Your Claude Code process stays exactly where it is, in a tmux window on your machine. CCBot simply reads its output and sends keystrokes to it. This means:

- **Switch from desktop to phone mid-conversation** — Claude is working on a refactor? Walk away, keep monitoring and responding from Telegram.
- **Switch back to desktop anytime** — Since the tmux session was never interrupted, just `tmux attach` and you're back in the terminal with full scrollback and context.
- **Run multiple sessions in parallel** — Each Telegram topic maps to a separate tmux window, so you can juggle multiple projects from one chat group.

Other Telegram bots for Claude Code typically wrap the Claude Code SDK to create separate API sessions. Those sessions are isolated — you can't resume them in your terminal. CCBot takes a different approach: it's just a thin control layer over tmux, so the terminal remains the source of truth and you never lose the ability to switch back.

## Features

- **Topic-based sessions** — Each Telegram topic maps 1:1 to a tmux window and Claude session
- **Real-time notifications** — Assistant responses, thinking content, tool use/result, and local command output
- **Interactive UI** — Navigate AskUserQuestion, ExitPlanMode, and Permission Prompts via inline keyboard
- **Send messages** — Forward text to Claude Code via tmux keystrokes
- **Slash command forwarding** — Any `/command` is forwarded to Claude Code (e.g. `/clear`, `/compact`, `/cost`)
- **Sessions dashboard** — Overview of all active sessions with status and quick actions
- **Message history** — Browse conversation history with pagination (newest first)
- **Auto-discovery** — Claude Code skills and custom commands appear in the Telegram menu automatically
- **Persistent state** — Thread bindings and read offsets survive restarts

## Quick Start

### 1. Prerequisites

- **tmux** — must be installed and available in PATH
- **Claude Code** — the CLI tool (`claude`) must be installed

### 2. Install

```bash
# Using uv (recommended)
uv tool install git+https://github.com/six-ddc/ccmux.git

# Or using pipx
pipx install git+https://github.com/six-ddc/ccmux.git
```

### 3. Configure

Create a Telegram bot via [@BotFather](https://t.me/BotFather), then enable **Threaded Mode** (BotFather > your bot > Settings > Bot Settings).

Create `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

### 4. Install the session hook

```bash
ccbot hook --install
```

This lets the bot auto-track which Claude session runs in each tmux window.

### 5. Run

```bash
ccbot
```

Open your Telegram group, create a new topic, send a message — a directory browser appears. Pick a project directory and you're connected to Claude Code.

## Documentation

| Guide                                          | Description                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------- |
| **[Getting Started](docs/getting-started.md)** | Detailed installation, configuration, and first session walkthrough |
| **[Guides](docs/guides.md)**                   | Multi-instance setup, manual tmux usage, data storage               |
| **[Contributing](docs/contributing.md)**       | Development setup, testing, code conventions                        |
