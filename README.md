# CCBot

[中文文档](README_CN.md)
[Русская документация](README_RU.md)

Control AI coding sessions remotely via Telegram — monitor, interact, and manage agent sessions running in tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## Why CCBot?

Claude Code runs in your terminal. When you step away from your computer — commuting, on the couch, or just away from your desk — the session keeps working, but you lose visibility and control.

CCBot solves this by letting you **seamlessly continue the same session from Telegram**. The key insight is that it operates on **tmux**, not the Claude Code SDK. Your Claude Code process stays exactly where it is, in a tmux window on your machine. CCBot simply reads its output and sends keystrokes to it. This means:

- **Switch from desktop to phone mid-conversation** — Claude is working on a refactor? Walk away, keep monitoring and responding from Telegram.
- **Switch back to desktop anytime** — Since the tmux session was never interrupted, just `tmux attach` and you're back in the terminal with full scrollback and context.
- **Run multiple sessions in parallel** — Each Telegram topic maps to a separate tmux window, so you can juggle multiple projects from one chat group.

Other Telegram bots for Claude Code typically wrap the Claude Code SDK to create separate API sessions. Those sessions are isolated — you can't resume them in your terminal. CCBot takes a different approach: it's just a thin control layer over tmux, so the terminal remains the source of truth and you never lose the ability to switch back.

In fact, CCBot itself was built this way — iterating on itself through Claude Code sessions monitored and driven from Telegram via CCBot.

## Features

- **Topic-based sessions** — Each Telegram topic maps 1:1 to a tmux window and agent session
- **Real-time notifications** — Get Telegram messages for assistant responses, thinking content, tool use/result, and local command output
- **Interactive UI** — Navigate AskUserQuestion, ExitPlanMode, and Permission Prompts via inline keyboard
- **Voice messages** — Voice messages are transcribed via OpenAI and forwarded as text
- **Send messages** — Forward text to the active CLI via tmux keystrokes
- **Slash command forwarding** — Optional forwarding for unknown `/command` to the active CLI
- **Create new sessions** — Start agent sessions from Telegram via directory browser
- **Resume sessions** — Pick up where you left off by resuming an existing Claude session in a directory
- **Kill sessions** — Close a topic to auto-kill the associated tmux window
- **Message history** — Browse conversation history with pagination (newest first)
- **Provider support** — Claude Code (`claude`) and Codex CLI (`codex`)
- **Session tracking** — Claude via `SessionStart` hook, Codex via rollout mapper
- **Persistent state** — Thread bindings and read offsets survive restarts

## Prerequisites

- **tmux** — must be installed and available in PATH
- **Agent CLI** — install one or both:
  - Claude Code (`claude`)
  - Codex CLI (`codex`)

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

## Configuration

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
| `CCBOT_PROVIDER`        | `claude`   | Session provider: `claude` or `codex`            |
| `CCBOT_AGENT_COMMAND`   | provider default (`claude`/`codex`) | Command to run in new windows |
| `CLAUDE_COMMAND`        | `claude`   | Backward-compatible alias for `CCBOT_AGENT_COMMAND` |
| `CCBOT_CODEX_SESSIONS_PATH` | `~/.codex/sessions` | Codex rollout root path |
| `CCBOT_FORWARD_PORTS` | _(none)_ | Comma-separated local ports to expose publicly on startup (e.g. `3000,5173`) |
| `CCBOT_FORWARD_SLASH`   | `true` | Forward unknown `/command` to CLI |
| `MONITOR_POLL_INTERVAL` | `2.0`      | Polling interval in seconds                      |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | Show hidden (dot) directories in directory browser |
| `OPENAI_API_KEY` | _(none)_ | OpenAI API key for voice message transcription |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI API base URL (for proxies or compatible APIs) |

Message formatting is always HTML via `chatgpt-md-converter` (`chatgpt_md_converter` package).
There is no runtime formatter switch to MarkdownV2.

> If running on a VPS where there's no interactive terminal to approve permissions, consider:
>
> ```
> CCBOT_AGENT_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

## Session Tracking Setup

### Claude Provider (Recommended)

Auto-install via CLI:

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

This writes window-session mappings to `$CCBOT_DIR/session_map.json` (`~/.ccbot/` by default), so the bot automatically tracks which Claude session is running in each tmux window — even after `/clear` or session restarts.

### Codex Provider

No hook installation is required. CCBot maps tmux windows to Codex rollout files from `~/.codex/sessions` (or `CCBOT_CODEX_SESSIONS_PATH`).

## Usage

```bash
# If installed via uv tool / pipx
ccbot

# If installed from source
uv run ccbot
```

### Provider Switching

Switch provider at startup:

```bash
# Claude Code
CCBOT_PROVIDER=claude CCBOT_AGENT_COMMAND=claude uv run ccbot

# Codex CLI
CCBOT_PROVIDER=codex CCBOT_AGENT_COMMAND=codex uv run ccbot
```

You can also place these values in `~/.ccbot/.env`.

### Port Forwarding

Expose local dev ports with CLI flags:

```bash
uv run ccbot --forward 3000
```

Multiple ports:

```bash
uv run ccbot --forward 3000 --forward 5173
```

Behavior:

- On startup, CCBot creates tunnel(s), sends real public URLs to all allowed users, and pins that message.
- On shutdown, CCBot stops tunnel(s) and unpins the message.
- Tunnel provider priority: `ngrok` -> `cloudflared` -> `localhost.run (ssh)`.

### Commands

**Bot commands:**

| Command       | Description                     |
| ------------- | ------------------------------- |
| `/start`      | Show welcome message            |
| `/history`    | Message history for this topic  |
| `/screenshot` | Capture terminal screenshot     |
| `/esc`        | Send Escape to interrupt active session |

**Claude provider commands (forwarded via tmux):**

| Command    | Description                  |
| ---------- | ---------------------------- |
| `/clear`   | Clear conversation history   |
| `/compact` | Compact conversation context |
| `/cost`    | Show token/cost usage        |
| `/help`    | Show Claude Code help        |
| `/memory`  | Edit CLAUDE.md               |

For `CCBOT_PROVIDER=claude`, unknown `/command` is forwarded as-is (e.g. `/review`, `/doctor`, `/init`).

### Topic Workflow

**1 Topic = 1 Window = 1 Session.** The bot runs in Telegram Forum (topics) mode.

**Creating a new session:**

1. Create a new topic in the Telegram group
2. Send any message in the topic
3. A directory browser appears — select the project directory
4. If the directory has existing Claude sessions, a session picker appears — choose one to resume or start fresh
5. A tmux window is created, agent command starts (with `--resume` when selecting a Claude session), and your pending message is forwarded

**Sending messages:**

Once a topic is bound to a session, just send text or voice messages in that topic — text gets forwarded to the active CLI via tmux keystrokes, and voice messages are transcribed then forwarded as text.

**Killing a session:**

Close (or delete) the topic in Telegram. The associated tmux window is automatically killed and the binding is removed.

### Message History

Navigate with inline buttons:

```
📋 [project-name] Messages (42 total)

───── 14:32 ─────

👤 fix the login bug

───── 14:33 ─────

I'll look into the login bug...

[◀ Older]    [2/9]    [Newer ▶]
```

### Notifications

The monitor polls session JSONL files every 2 seconds and sends notifications for:

- **Assistant responses** — model text replies
- **Thinking content** — Shown as expandable blockquotes
- **Tool use/result** — Summarized with stats (e.g. "Read 42 lines", "Found 5 matches")
- **Local command output** — stdout from commands like `git status`, prefixed with `❯ command_name`

Notifications are delivered to the topic bound to the session's window.

## Running in tmux

### Option 1: Create via Telegram (Recommended)

1. Create a new topic in the Telegram group
2. Send any message
3. Select the project directory from the browser

### Option 2: Create Manually

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
# Then start your CLI in the new window
claude
# or
codex
```

The window must be in the `ccbot` tmux session (configurable via `TMUX_SESSION_NAME`). Claude provider uses hooks for session mapping; Codex provider uses rollout mapping.

## Resume Existing Session

To attach CCBot to an existing Codex session, run:

```bash
CCBOT_PROVIDER=codex \
CCBOT_AGENT_COMMAND='codex resume <session-id>' \
uv run ccbot
```

Example:

```bash
CCBOT_PROVIDER=codex \
CCBOT_AGENT_COMMAND='codex resume 019c9eef-c5f7-7dc2-9e92-de59a1c3cd28' \
uv run ccbot
```

## Data Storage

| Path                            | Description                                                             |
| ------------------------------- | ----------------------------------------------------------------------- |
| `$CCBOT_DIR/state.json`         | Thread bindings, window states, display names, and per-user read offsets |
| `$CCBOT_DIR/session_map.json`   | Provider mappings `{tmux_session:window_id: {session_id, cwd, ...}}` |
| `$CCBOT_DIR/monitor_state.json` | Monitor byte offsets per session (prevents duplicate notifications)     |
| `~/.claude/projects/`           | Claude Code session data (read-only)                                    |
| `~/.codex/sessions/`            | Codex rollout session logs (read-only)                                  |

## File Structure

```
src/ccbot/
├── __init__.py            # Package entry point
├── main.py                # CLI dispatcher (hook subcommand + bot bootstrap)
├── hook.py                # Hook subcommand for session tracking (+ --install)
├── config.py              # Configuration from environment variables
├── bot.py                 # Telegram bot setup, command handlers, topic routing
├── session.py             # Session management, state persistence, message history
├── session_monitor.py     # JSONL file monitoring (polling + change detection)
├── monitor_state.py       # Monitor state persistence (byte offsets)
├── transcript_parser.py   # Claude Code JSONL transcript parsing
├── terminal_parser.py     # Terminal pane parsing (interactive UI + status line)
├── html_converter.py      # Markdown → Telegram HTML conversion + HTML-aware splitting
├── screenshot.py          # Terminal text → PNG image with ANSI color support
├── transcribe.py          # Voice-to-text transcription via OpenAI API
├── utils.py               # Shared utilities (atomic JSON writes, JSONL helpers)
├── tmux_manager.py        # Tmux window management (list, create, send keys, kill)
├── fonts/                 # Bundled fonts for screenshot rendering
└── handlers/
    ├── __init__.py        # Handler module exports
    ├── callback_data.py   # Callback data constants (CB_* prefixes)
    ├── directory_browser.py # Directory browser inline keyboard UI
    ├── history.py         # Message history pagination
    ├── interactive_ui.py  # Interactive UI handling (AskUser, ExitPlan, Permissions)
    ├── message_queue.py   # Per-user message queue + worker (merge, rate limit)
    ├── message_sender.py  # safe_reply / safe_edit / safe_send helpers
    ├── response_builder.py # Response message building (format tool_use, thinking, etc.)
    └── status_polling.py  # Terminal status line polling
```

## Contributors

Thanks to all the people who contribute! We encourage using Claude Code to collaborate on contributions.

<a href="https://github.com/six-ddc/ccmux/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=six-ddc/ccmux" />
</a>
