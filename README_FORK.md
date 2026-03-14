# CCBot Fork: Russian Edition

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Telegram Bot API](https://img.shields.io/badge/Telegram%20Bot%20API-7.x-26A5E4)

A hardened, Russian-localized fork of [six-ddc/ccmux](https://github.com/six-ddc/ccmux) — a Telegram bridge for Claude Code sessions running in tmux.

---

## What's Different

The upstream bot forwards every tool invocation, bash command output, and internal MCP response to Telegram — unusable in practice. This fork introduces **Clean Output Mode** that suppresses tool noise and delivers only Claude's final responses, paired with full Russian localization, Deepgram Nova-3 voice transcription, automatic file delivery, emoji reaction feedback, and a security hardening pass that fixed 19 vulnerabilities identified by a four-agent audit.

---

## Quick Start

**Prerequisites:** tmux, Claude Code CLI (`claude`), Python 3.12+, [uv](https://docs.astral.sh/uv/)

**1. Clone and install**

```bash
git clone <this-repo> ccbot
cd ccbot
uv sync
```

**2. Create your bot**

Talk to [@BotFather](https://t.me/BotFather), create a bot, and enable **Threaded Mode** (Settings → Bot Settings → Threaded Mode).

**3. Configure**

```bash
cp .env.example ~/.ccbot/.env
# Edit ~/.ccbot/.env — at minimum set TELEGRAM_BOT_TOKEN and ALLOWED_USERS
```

**4. Install the session hook**

```bash
uv run ccbot hook --install
```

**5. Run**

```bash
uv run ccbot
```

Create a topic in your Telegram group, send any message, pick a directory — you're connected.

---

## Features

### Clean Output Mode

**Problem:** The upstream bot forwards every `Bash(...)`, `Read(...)`, `ToolSearch(...)`, MCP JSON response (including API keys), and internal `thinking` block to Telegram. Running a real project generates hundreds of noise messages per session.

**Solution:** `CCBOT_CLEAN_OUTPUT=true` (the default) filters at the JSONL parse layer before anything reaches Telegram.

**Filtered out:**
- `tool_use` messages — Bash, Read, Write, Grep, MCP invocations
- `tool_result` messages — command stdout, file contents, JSON API responses
- `thinking` blocks — Claude's internal chain-of-thought
- `local_command` messages — hook outputs, skill activations

**Passes through:**
- Final assistant text responses
- Interactive UI requiring user input: `AskUserQuestion`, `ExitPlanMode`
- Images from tool results (screenshots, generated images)
- Documents written by the `Write` tool (sent as Telegram file attachments)

While Claude is working, a single "⏳ Работаю…" message is shown per session. It is edited in-place when Claude responds — no message flood.

To restore the original verbose output: `CCBOT_CLEAN_OUTPUT=false`.

---

### Automatic File Sending

When Claude writes a document file via the `Write` tool, the bot automatically sends it to the correct Telegram topic as a file attachment.

**Supported extensions:** `.md`, `.txt`, `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.csv`, `.html`, `.htm`, `.rtf`, `.epub`

**Smart phrase detection:** Messages containing natural-language delivery requests (e.g., "пришли в тг", "скинь в тг", "отправь в телеграм" — 11 variants) automatically append a system hint instructing Claude to save the result as a file first. Claude writes the file; the bot sends it.

**Security constraints applied to every sent file:**
- Path resolved via `Path.expanduser().resolve()` before any operation
- `allowed_roots` boundary check at extraction (session_monitor) and at send (bot)
- Sensitive filename blocklist: `.env`, `credentials`, `id_rsa`, `id_ed25519`, `id_dsa`, `.netrc`, `.pgpass`, `session_map.json`, `state.json`, `monitor_state.json`
- Config directory (`~/.ccbot/`) excluded
- Size: must be > 0 bytes and < 50 MB
- Deduplication by resolved path — same file via different paths will not be sent twice

---

### Emoji Reactions

React to any bot message with an emoji to send a short feedback string to Claude in that session. Routing is tracked per `message_id → (thread_id, window_id)` with 14-day TTL and a 10k entry hard cap.

| Reaction | Message sent to Claude |
|----------|----------------------|
| ❤️ | Спасибо, отлично! |
| 🔥 | Огонь, продолжай! |
| 👍 | Хорошо, принято. |
| 👎 | Не то, переделай. |
| 💯 | Идеально, 100 баллов! |
| 👏 | Браво, так держать! |
| 🤩 | Вау, впечатляет! |
| 😁 | Весело, мне нравится! |
| 🙏 | Спасибо большое! |
| 👌 | Ок, всё понятно. |
| 🤔 | Хм, подумай ещё раз. |
| 😢 | Грустно, давай по-другому. |
| 🎉 | Круто, празднуем! |
| 🆒 | Круто! |
| 🤣 | Смешно получилось! |
| 😱 | Ого, неожиданно! |
| 💔 | Нет, это плохо. Переделай. |
| 🤮 | Фу, ужасно. Убери это. |
| 💩 | Дерьмо, переделывай полностью. |

Requires `message_reaction` in `allowed_updates` — already registered in `main.py`.

---

### Auto-Cleanup of Dead Sessions

After `/exit` or a Claude crash, tmux windows previously accumulated as orphaned shells. This feature detects termination and cleans up automatically.

**Detection:** Status polling (every 1 s) calls `pgrep -P <pane_pid>`. If the shell has no child processes, Claude has exited. This check cannot be spoofed by process name.

**Grace period:** The "not running" state must persist for 5 consecutive seconds before cleanup fires. This prevents false positives during brief process transitions (e.g., Claude restarting after `/clear`). The grace timer resets if Claude is detected alive again.

**Cleanup sequence:**
1. Kill tmux window
2. Unbind thread from topic
3. Clear topic state (status messages, interactive UI widgets)
4. Send notification: "Сессия завершена. Напиши сюда чтобы начать новую или возобновить."

**Resume flow:** The user writes in the same topic, the directory browser appears, and the session picker lists previous Claude sessions for `--resume`.

---

### Voice Transcription (Deepgram Nova-3)

Replaces the upstream OpenAI Whisper (`gpt-4o-transcribe`) with [Deepgram Nova-3](https://developers.deepgram.com/).

| Parameter | Value |
|-----------|-------|
| Endpoint | `https://api.deepgram.com/v1/listen` |
| Model | `nova-3` |
| Language | `ru` |
| Auth header | `Token <key>` |
| Input | Raw OGG bytes (`Content-Type: audio/ogg`) |
| Output path | `results.channels[0].alternatives[0].transcript` |

Set `DEEPGRAM_API_KEY` to enable. Voice messages are silently skipped without a key. Raw API error bodies are never exposed in exception messages.

---

### Russian Localization

Every user-facing string is translated to Russian.

**Bot command menu (13 commands registered with BotFather):**

| Command | Description |
|---------|-------------|
| `/start` | Приветствие и справка |
| `/history` | История сообщений сессии |
| `/screenshot` | Скриншот терминала |
| `/esc` | Прервать Claude (Escape) |
| `/kill` | Завершить сессию и удалить топик |
| `/unbind` | Отвязать топик (окно останется) |
| `/usage` | Остаток лимита Claude Code |
| `/clear` | Очистить историю диалога |
| `/compact` | Сжать контекст разговора |
| `/cost` | Показать расход токенов |
| `/help` | Справка Claude Code |
| `/memory` | Редактировать CLAUDE.md |
| `/model` | Сменить модель ИИ |

**UI elements:** Directory browser, window picker, session picker, all error messages, callback query answers, and status notifications are fully localized.

---

## Security

A four-parallel-agent security audit identified 19 vulnerabilities. Key hardening applied:

**Input validation:**
- Message length hard-capped at 4096 characters at a single enforcement point covering all four input handlers
- Null bytes (`\x00`) and carriage returns (`\r`) stripped from tmux `send_keys` to prevent tmux injection
- Session IDs from `session_map.json` validated with UUID regex and wrapped in `shlex.quote()` before `--resume` use

**Rate limiting:**
- 30 messages per 60 seconds per user, applied to text, photo, voice, and forwarded command handlers
- Periodic cleanup every 50 calls (amortized O(1)); stale entries evicted after 2× the window

**Directory browser:**
- Navigation constrained by `CCBOT_ALLOWED_ROOTS` using `Path.is_relative_to()` (not string prefix comparison)
- Race condition closed: path re-validated in the `CB_DIR_CONFIRM` callback, preventing Up+Confirm bypass

**Process detection:**
- `is_claude_running()` uses `pgrep -P <pane_pid>` (checks shell children), not process name matching

**Credential protection:**
- `SENSITIVE_ENV_VARS` covers: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USERS`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `DEEPGRAM_API_KEY`
- All sensitive vars scrubbed from `os.environ` at startup — child processes (Claude Code via tmux) never inherit them
- Log file created with `chmod 600`; bot token redacted to 4 chars + `****` in all log output

**Memory safety:**
- `_msg_thread_map` capped at 10k entries with 14-day TTL; forced eviction to 5k on overflow

---

## Configuration

### `.env` loading priority

1. `.env` in current working directory
2. `$CCBOT_DIR/.env` (default: `~/.ccbot/.env`)

### Full environment variable reference

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Yes | Bot token from @BotFather |
| `ALLOWED_USERS` | — | Yes | Comma-separated Telegram user IDs |
| `CCBOT_DIR` | `~/.ccbot` | No | Config and state directory |
| `TMUX_SESSION_NAME` | `ccbot` | No | tmux session name to manage |
| `CLAUDE_COMMAND` | `claude` | No | Command executed in new tmux windows |
| `MONITOR_POLL_INTERVAL` | `2.0` | No | JSONL polling interval in seconds (min 0.5) |
| `CCBOT_CLEAN_OUTPUT` | `true` | No | Filter tool noise; only deliver final responses |
| `CCBOT_SHOW_USER_MESSAGES` | `false` | No | Echo user messages back with a prefix |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | No | Show dot-directories in the browser |
| `CCBOT_ALLOWED_ROOTS` | `~` | No | Comma-separated directory boundaries for browser and file sending |
| `CCBOT_CLAUDE_PROJECTS_PATH` | `~/.claude/projects` | No | Override path to Claude session files |
| `CLAUDE_CONFIG_DIR` | — | No | Alternative Claude config dir (used to derive projects path) |
| `DEEPGRAM_API_KEY` | — | No | Deepgram API key for voice transcription |
| `OPENAI_API_KEY` | — | No | Legacy: kept for env scrubbing only; not used for transcription |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | No | Legacy OpenAI base URL; scrubbed from child environment |

**Recommended production setting:**

```ini
CCBOT_ALLOWED_ROOTS=~/Documents/Dev,~/projects
CCBOT_CLEAN_OUTPUT=true
DEEPGRAM_API_KEY=your_key_here
```

---

## Architecture

```
User
  |
  | Telegram message / emoji reaction / voice note
  v
Telegram Bot API
  |
  v
bot.py  ──────────────────────────────────────────────────
  |  Rate limiter (30 msg/60s per user)                  |
  |  Input sanitization (length, null bytes, CR)         |
  |  Topic → window_id routing (thread_bindings)         |
  |                                                       |
  | tmux send-keys                                        |
  v                                                       |
TmuxManager                                              |
  |  pgrep-based liveness check                          |
  |  shlex.quote() for session IDs                       |
  v                                                       |
tmux window (one per topic)                              |
  |                                                       |
  | Claude Code process writes JSONL                     |
  v                                                       |
~/.claude/projects/<hash>/<session>.jsonl                |
  ^                                                       |
  | poll every 2s (incremental byte-offset reads)        |
  |                                                       |
SessionMonitor ──────────────────────────────────────────
  |  Clean output filter (tool_use / tool_result / thinking suppressed)
  |  File detection (Write tool → auto-send document)
  |  Working status deduplication
  |
  | NewMessage callback
  v
bot.py message queue (per-user FIFO, merge, rate limit)
  |
  v
Telegram topic (thread_id bound to originating window)
```

**State files** (all under `$CCBOT_DIR`, default `~/.ccbot/`):

| File | Contents |
|------|----------|
| `state.json` | Thread bindings, window states, display names, read offsets |
| `session_map.json` | Hook-generated `{tmux_session:window_id → session_id, cwd}` |
| `monitor_state.json` | Byte offsets per JSONL file (prevents duplicate delivery on restart) |
| `ccbot.log` | Rotating log, max 1 MB × 4 files, `chmod 600` |

---

## Contributing

1. Fork and create a feature branch
2. Run the full check suite before submitting:

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pyright src/ccbot/
uv run pytest
```

All four commands must pass with zero errors. The project targets Python 3.12+ with strict Pyright settings.

Pull requests that introduce `TODO`, `FIXME`, debug `print()` calls, or commented-out code will be declined.

---

## License

MIT — see [LICENSE](LICENSE).

Upstream project: [six-ddc/ccmux](https://github.com/six-ddc/ccmux) (MIT).
