# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              INBOUND FLOW                                   │
│                                                                             │
│  User (Telegram)                                                            │
│       │  text / voice / photo / reaction / command                         │
│       ▼                                                                     │
│  bot.py  ──── rate limit (30 msg/60s per user) ────►  BLOCKED              │
│       │  allowed_users check                                                │
│       │  input sanitization (strip \x00, \r)                               │
│       │  length check (4096 chars)                                          │
│       ▼                                                                     │
│  session.py (SessionManager)                                                │
│       │  thread_id → window_id  (state.json)                               │
│       ▼                                                                     │
│  tmux_manager.py (TmuxManager)                                              │
│       │  send_keys() → tmux pane                                            │
│       ▼                                                                     │
│  Claude Code (TUI process inside tmux window)                               │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              OUTBOUND FLOW                                  │
│                                                                             │
│  Claude Code writes JSONL entries                                           │
│       │  ~/.claude/projects/<hash>/<session_id>.jsonl                      │
│       ▼                                                                     │
│  hook.py (SessionStart hook)                                                │
│       │  writes window_id → session_id mapping                             │
│       ▼                                                                     │
│  session_map.json  (~/.ccbot/)                                              │
│       │                                                                     │
│       ▼                                                                     │
│  session_monitor.py (SessionMonitor)                                        │
│       │  polls every 2s                                                     │
│       │  mtime + byte offset cache (reads only new bytes)                  │
│       │  transcript_parser.py → parses JSONL entries                       │
│       │  clean_output filter (drops tool_use, tool_result, thinking)       │
│       │  file detection (Write tool → auto-send document)                  │
│       ▼                                                                     │
│  bot.py (NewMessage callback)                                               │
│       │  session_id → window_id → thread_id                                │
│       │  message_queue.py (per-user FIFO, merge ≤3800 chars)               │
│       │  handlers/message_sender.py (safe_reply / safe_edit / safe_send)   │
│       │  markdown_v2.py + telegram_sender.py (4096 char split)             │
│       ▼                                                                     │
│  User (Telegram topic)                                                      │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                      SESSION CREATION FLOW (new topic)                      │
│                                                                             │
│  User sends first message in unbound topic                                  │
│       ▼                                                                     │
│  handlers/directory_browser.py                                              │
│       │  inline keyboard: navigate dirs within allowed_roots                │
│       │  session picker: shows existing Claude sessions to resume           │
│       ▼                                                                     │
│  tmux_manager.create_window(work_dir, resume_session_id?)                   │
│       │  creates tmux window, starts `claude [--resume <uuid>]`             │
│       ▼                                                                     │
│  Claude fires SessionStart hook → hook.py updates session_map.json         │
│       ▼                                                                     │
│  SessionMonitor detects new session, begins polling JSONL                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Module Inventory

| File | Responsibility | Key Classes / Functions |
|------|---------------|------------------------|
| `main.py` | CLI entry point; wires up bot + monitor; configures rotating log | `main()` |
| `config.py` | Loads `.env`, validates required vars, scrubs secrets from env | `Config`, `config` singleton, `SENSITIVE_ENV_VARS` |
| `bot.py` | Telegram handlers: text, photo, voice, reactions, commands, file sending | `setup_handlers()`, `handle_new_message()` |
| `session.py` | Thread↔window bindings; session map loading; history retrieval | `SessionManager`, `session_manager` singleton |
| `session_monitor.py` | Async poll loop: reads JSONL, filters output, emits `NewMessage` | `SessionMonitor`, `NewMessage`, `SessionInfo` |
| `monitor_state.py` | Persists byte offsets per JSONL file across restarts | `MonitorState`, `TrackedSession` |
| `tmux_manager.py` | libtmux wrapper: create/kill windows, send_keys, capture_pane | `TmuxManager`, `tmux_manager` singleton |
| `hook.py` | Claude Code `SessionStart` hook: writes `session_map.json`; `--install` helper | `hook_main()`, `_install_hook()` |
| `transcript_parser.py` | Parses raw JSONL entries; pairs tool_use↔tool_result; extracts images | `TranscriptParser` |
| `terminal_parser.py` | Detects interactive UI types and status line from pane capture | `detect_interactive_ui()`, `parse_status_line()` |
| `markdown_v2.py` | Converts Markdown to Telegram MarkdownV2; expandable quotes for thinking | `md_to_telegram()` |
| `telegram_sender.py` | Splits long messages at 4096-char boundary respecting quote atomicity | `split_message()` |
| `screenshot.py` | Renders ANSI terminal capture to PNG using PIL and custom fonts | `render_terminal_screenshot()` |
| `transcribe.py` | Voice-to-text via Deepgram Nova-3 REST API | `transcribe_voice()` |
| `utils.py` | `ccbot_dir()`, `atomic_write_json()`, `read_cwd_from_jsonl()` | utility functions |
| `handlers/bot.py` | n/a — handlers live in `bot.py` and handlers/ subdirectory | — |
| `handlers/message_sender.py` | `safe_reply/safe_edit/safe_send`; MarkdownV2 with plaintext fallback | `safe_reply()`, `rate_limit_send()` |
| `handlers/message_queue.py` | Per-user FIFO queue + worker; message merging; tool_use↔result pairing | `MessageQueue`, `enqueue()` |
| `handlers/status_polling.py` | 1s background loop polls terminal status; auto-cleanup of dead sessions | `start_status_polling()` |
| `handlers/response_builder.py` | Paginates history; formats pages with inline keyboard navigation | `build_history_page()` |
| `handlers/interactive_ui.py` | Renders `AskUserQuestion` / `ExitPlanMode` / Permission inline keyboards | `render_interactive_ui()` |
| `handlers/directory_browser.py` | Directory navigation UI; session picker for resume flow | `show_directory_browser()` |
| `handlers/cleanup.py` | Tears down topic state: kills window, unbinds thread, clears UI | `cleanup_topic()` |
| `handlers/callback_data.py` | String constants for all callback data prefixes (≤64 bytes) | constants |

## Data Flow

### Outbound (User → Claude)

1. Telegram delivers message to `bot.py` handler (`text_handler`, `photo_handler`, `voice_handler`, `forward_command_handler`, `reaction_handler`).
2. `bot.py` checks `config.allowed_users`; rejects silently if not allowed.
3. Rate limiter checks message count for `user_id` in the last 60 seconds (max 30); returns "too many messages" if exceeded.
4. Input sanitized: null bytes and carriage returns stripped; length checked against 4096 chars.
5. Voice messages transcribed via `transcribe.py` (Deepgram Nova-3) before proceeding.
6. Smart phrase detection: if message contains a Russian "send to Telegram" phrase, a system hint is appended instructing Claude to save to file.
7. `session_manager.get_window_for_thread(user_id, thread_id)` resolves the bound tmux `window_id`.
8. If no binding exists, `directory_browser.py` initiates the session creation flow.
9. `tmux_manager.send_keys(window_id, text)` sends sanitized text + Enter to the Claude Code TUI pane.

### Inbound (Claude → User)

1. Claude Code writes conversation turns as JSONL entries to `~/.claude/projects/<hash>/<session_id>.jsonl`.
2. `SessionMonitor._monitor_loop()` polls every 2 seconds.
3. Per-cycle: reads `session_map.json` to get active `window_id → session_id` pairs.
4. For each active session: checks file mtime + size; skips if unchanged.
5. New bytes read from last byte offset via `_read_new_lines()`; partial lines (concurrent writes) are deferred to next cycle.
6. `TranscriptParser.parse_entries()` pairs tool_use with tool_result across poll cycles.
7. If `CCBOT_CLEAN_OUTPUT=true` (default): drops `tool_use`, `tool_result`, `thinking`, `local_command` entries; emits a single "working" status message per batch of tool work.
8. Interactive tools (`AskUserQuestion`, `ExitPlanMode`) and images always pass through regardless of clean_output.
9. `Write` tool entries trigger file path extraction → allowed_roots + sensitive name check → `NewMessage(content_type="file")`.
10. `NewMessage` callback resolves `session_id → window_id → thread_id` via `session_manager`.
11. Message enqueued in per-user `MessageQueue`; worker merges consecutive text messages ≤3800 chars.
12. `message_sender.safe_reply/safe_send` renders MarkdownV2 (with plaintext fallback) and sends to the Telegram topic.

## State Files

| File | Location | Purpose | Format |
|------|----------|---------|--------|
| `state.json` | `~/.ccbot/` | Thread↔window bindings, window display names, message history read offsets | JSON object |
| `session_map.json` | `~/.ccbot/` | `"tmux_session:@window_id"` → `{session_id, cwd, window_name}`; written by hook | JSON object |
| `monitor_state.json` | `~/.ccbot/` | Per-session byte offsets for incremental JSONL reads | JSON object |
| `ccbot.log` | `~/.ccbot/` | Rotating log (1MB × 4 files), `chmod 600` | Text, `RotatingFileHandler` |
| `<session_id>.jsonl` | `~/.claude/projects/<hash>/` | Claude Code conversation turns (read-only by CCBot) | JSONL |
| `sessions-index.json` | `~/.claude/projects/<hash>/` | Index of session IDs and file paths per project directory | JSON |

## Security Model

See [SECURITY.md](SECURITY.md) for the full threat model and hardening details.

Brief summary:

- **Allowed users**: `ALLOWED_USERS` env var — hardcoded set of Telegram user IDs. All handlers check this first.
- **Rate limiting**: 30 messages per 60-second window per user, in-memory with amortized cleanup.
- **Input sanitization**: null bytes and `\r` stripped before `send_keys`; UUID regex + `shlex.quote()` for `--resume` session IDs.
- **Directory browser**: navigation bounded by `CCBOT_ALLOWED_ROOTS`; `Path.is_relative_to()` boundary check; race condition fixed with re-validation at confirm.
- **File sending**: resolved paths checked against `allowed_roots`; sensitive filename blocklist; config dir excluded; `.json`/`.xml` excluded; size 0–50MB.
- **Secret isolation**: `SENSITIVE_ENV_VARS` scrubbed from `os.environ` and tmux session environment at startup so Claude Code subprocesses cannot inherit them.

## Configuration Hierarchy

```
.env (local, cwd)            highest priority
  └── .env (~/.ccbot/)       fallback
        └── Config class     captures values, then scrubs SENSITIVE_ENV_VARS
              └── runtime    all modules import `config` singleton
```

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | required | Telegram bot token |
| `ALLOWED_USERS` | required | Comma-separated Telegram user IDs |
| `CCBOT_DIR` | `~/.ccbot/` | Override config/state directory |
| `TMUX_SESSION_NAME` | `ccbot` | tmux session name |
| `CLAUDE_COMMAND` | `claude` | Command to start Claude Code |
| `CCBOT_CLAUDE_PROJECTS_PATH` | `~/.claude/projects/` | Path to Claude Code projects |
| `CLAUDE_CONFIG_DIR` | — | Alternative Claude config dir (appends `/projects`) |
| `MONITOR_POLL_INTERVAL` | `2.0` | JSONL poll interval in seconds (min 0.5) |
| `CCBOT_CLEAN_OUTPUT` | `true` | Filter tool spam; show only final responses |
| `CCBOT_ALLOWED_ROOTS` | `~` | Comma-separated directory browser boundaries |
| `CCBOT_SHOW_USER_MESSAGES` | `false` | Echo user messages back with prefix |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | Show hidden dirs in directory browser |
| `DEEPGRAM_API_KEY` | — | Deepgram key for voice transcription (optional) |
| `OPENAI_API_KEY` | — | Legacy; scrubbed from env, not actively used |
