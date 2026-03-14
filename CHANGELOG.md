# Changelog

## [Fork] 2026-03-15 — Rich Progress Status (Live Tool Activity Display)

In clean output mode, instead of showing just "⏳ Работаю…", the bot now displays what Claude is actually doing in real-time:

- `📖 Читаю src/auth.py` — when Claude reads a file
- `✏️ Редактирую config.ts` — when Claude edits code
- `⚡ npm test` — when Claude runs a command
- `🔍 Ищу: authentication` — when Claude searches code
- `🤖 Агент: code review` — when Claude launches a sub-agent
- `🧠 Думаю…` — when Claude is reasoning
- `🔌 tool_name` — for MCP tool calls

Status updates in-place via `edit_message_text` — zero message spam. Updates on each new tool call, clears when Claude responds with text.

---

## [Fork] 2026-03-15 — Multi-message Input Batching

When a user sends multiple messages rapidly (within 1.5 seconds), they are now combined into a single prompt before being sent to Claude Code. This prevents Claude from processing each message as a separate prompt, which caused fragmented responses and wasted context.

- Debounce timer per (user, thread): first message starts 1.5s timer, subsequent messages reset it
- Messages joined with `\n` and sent as one `send_to_window` call
- Bypass: `!` bash commands and interactive UI responses always send immediately
- Configurable: `CCBOT_INPUT_BATCH_SECONDS` (default 1.5, 0 = disabled)

---

## [Fork] 2026-03-15 — Idle Detection + Smart Re-notification

When Claude finishes responding and the user doesn't reply within 2 minutes, the bot sends a reminder: "Claude ожидает ваш ответ." This solves the problem of missed Claude responses when the user is away.

- Tracks `last_user_activity` and `last_claude_response` per session
- Sends ONE reminder per idle period (resets when user replies)
- Configurable: `CCBOT_IDLE_REMINDER_SECONDS` (default 120, 0 = disabled)
- Also fixed semgrep credential-in-log finding in config.py (removed token reference from log format)

---

## [Fork] 2026-03-15 — Document Upload Handler

Users can now send files (code, configs, PDFs, docs) to Claude Code via Telegram:
- Downloads document to `~/.ccbot/documents/` with timestamp prefix
- Forwards file path + optional caption to Claude Code session
- Extension allowlist: 50+ formats (code, text, documents — no executables)
- Sensitive filename blocklist (`.env`, credentials, keys)
- Size limit: 20 MB
- Same auth, rate limiting, and input validation as other handlers

---

## [Fork] 2026-03-15 — Message Edit Forwarding

When a user edits a previously sent message in Telegram, the corrected text is now forwarded to Claude Code with a "(Исправление)" prefix. Previously, edits were silently ignored.

- Added `edited_message_handler` with full auth, rate limiting, and input validation
- Added `"edited_message"` to `allowed_updates` in polling config
- Same security pipeline as regular messages (sanitization via `send_to_window`)

---

## [Fork] 2026-03-15 — /health Diagnostic Command

New `/health` command shows bot diagnostics directly in Telegram:
- Uptime (hours, minutes, seconds)
- Active sessions count
- tmux server status and window count
- Session monitor and status polling state
- Memory usage (RSS in MB)
- Python version

| File | Changes |
|------|---------|
| `src/ccbot/bot.py` | Added `health_command`, `_bot_start_time` tracking, registered handler |

---

## [Fork] 2026-03-14 — Graceful Shutdown with User Notifications

When the bot receives SIGTERM/SIGINT (e.g. Railway redeploy, systemd restart), it now:
1. Sends "Бот перезапускается. Ваши сессии сохранены." to all bound topics
2. Writes a clean shutdown marker to config directory
3. On next startup, detects the marker and sends "Бот снова в сети. Ваши сессии восстановлены."

| File | Changes |
|------|---------|
| `src/ccbot/bot.py` | Enhanced `post_init` and `post_shutdown` with user notifications |
| `src/ccbot/utils.py` | Added `write_shutdown_marker()` and `read_and_clear_shutdown_marker()` |
| `tests/ccbot/test_utils.py` | Added 4 tests for shutdown marker lifecycle |

---

## [Fork] 2026-03-14 — Russian Edition + Security Hardening + Deepgram + Clean Output

690 lines added, 157 removed across 15 files. Full security audit (4 parallel agents, 19 findings fixed).

---

### Security Hardening (19 vulnerabilities found and fixed)

**Input Validation**
- Message length limit (4096 chars) in `send_to_window` — single enforcement point for all 4 handlers
- Null byte (`\x00`) and carriage return (`\r`) stripped from tmux `send_keys` input — prevents tmux injection
- UUID regex validation + `shlex.quote()` for `--resume` session IDs in `create_window` — prevents command injection via compromised `session_map.json`

**Rate Limiting**
- User-level rate limiting: 30 messages per 60 seconds
- Applied to ALL input channels: `text_handler`, `photo_handler`, `voice_handler`, `forward_command_handler`
- Periodic cleanup every 50 calls (not every call — O(1) amortized instead of O(n) per request)
- In-memory with auto-eviction of stale entries after 2x window

**Directory Browser Security**
- Navigation restricted by `CCBOT_ALLOWED_ROOTS` env var (default: home directory)
- Uses `Path.is_relative_to()` for correct boundary checking (not string comparison)
- Parent traversal (`..` button) blocked at allowed_roots boundary
- **Race condition fix**: path validated in `CB_DIR_CONFIRM` callback — prevents Up+Confirm bypass

**File Sending Security**
- All file paths resolved via `Path.expanduser().resolve()` before any operation
- Double `allowed_roots` check: in `session_monitor.py` (extraction) and `bot.py` (sending)
- Sensitive filename blocklist: `.env`, `credentials`, `id_rsa`, `id_ed25519`, `id_dsa`, `.netrc`, `.pgpass`, `session_map.json`, `state.json`, `monitor_state.json`
- Config directory (`~/.ccbot/`) excluded from auto-send
- `.json` and `.xml` removed from sendable extensions (too many credential files use these)
- Size validation: must be > 0 bytes and < 50MB
- Path-based deduplication (resolved, not string) — same file via different paths won't double-send
- Symlinks followed and resolved — path check happens on real path

**Credential Protection**
- `SENSITIVE_ENV_VARS` expanded: added `OPENAI_BASE_URL` and `DEEPGRAM_API_KEY`
- All sensitive vars scrubbed from `os.environ` and tmux session environment
- Bot token redacted in logs: 4 chars + `****` (was 8 chars)
- Deepgram API error responses no longer expose raw response body in exceptions
- Log file created with `chmod 600` (owner-only, was world-readable)

**Thread/Session Management**
- Stale thread auto-unbind: `BadRequest("thread not found")` triggers unbind — uses exact match, not substring
- Bounded memory: `_msg_thread_map` hard-capped at 10k entries with TTL eviction (14 days) and forced eviction to 5k on overflow
- `_tool_msg_ids` cleanup on topic teardown

**Process Management**
- `is_claude_running()` uses `pgrep -P <pane_pid>` to check shell children — can't be spoofed by process name
- 5-second grace period before auto-killing dead sessions — prevents false kills during brief process transitions
- Grace timer cleared when Claude detected as alive again

---

### Clean Output Mode (new, default: on)

Solves the core UX problem: original CCBot forwards ALL Claude Code output to Telegram — every `Bash(find ...)`, `ToolSearch(...)`, `Thinking...`, MCP server JSON responses with API keys visible. Unusable for real work.

**What's filtered out:**
- `tool_use` messages (Bash commands, Read/Write/Grep calls, MCP tool invocations)
- `tool_result` messages (command output, file contents, JSON API responses)
- `thinking` blocks (Claude's internal reasoning)
- `local_command` messages (skill activations, hook outputs)

**What passes through:**
- Final assistant text responses (Claude's actual answers)
- Interactive UI tools: `AskUserQuestion`, `ExitPlanMode` (require user response)
- Images from tool results (screenshots, generated images)
- File documents (auto-sent on Write tool, see below)

**Working status:**
- Single updatable "⏳ Работаю…" message instead of tool spam
- Deduplicated per session — appears once, disappears when Claude responds
- Uses Telegram's `edit_message_text` for in-place updates (no message flood)

**Config:** `CCBOT_CLEAN_OUTPUT=true` (default) / `false` to restore original verbose output

---

### Automatic File Sending to Telegram

When Claude Code writes a document file via the `Write` tool, the file is automatically sent to the correct Telegram topic as a document.

**Supported extensions:** `.md`, `.txt`, `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.csv`, `.html`, `.htm`, `.rtf`, `.epub`

**Smart phrase detection:** Messages containing Russian phrases like "пришли в тг", "скинь в тг", "отправь в телеграм" (11 variants) automatically append a system hint telling Claude to save results to a file. Claude writes the file → bot sends it.

**Security:** Path resolved, allowed_roots checked, sensitive names blocked, config dir excluded, deduplication by resolved path (see Security section).

---

### Emoji Reactions as Feedback

React to any bot message with an emoji to send a short feedback message to Claude Code.

| Emoji | Message sent to Claude |
|-------|----------------------|
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

**Routing:** Each sent message tracks `message_id → (thread_id, window_id)` for accurate multi-topic routing. Falls back to first bound topic if tracking miss. 14-day auto-cleanup with hard cap at 10k entries.

**Requires:** `message_reaction` in `allowed_updates` (added in `main.py`).

---

### Auto-Cleanup of Dead Sessions

Solves the resource leak: after `/exit` or Claude crash, tmux windows stayed alive with dead shells, accumulating indefinitely.

**Detection:** Status polling (every 1s) checks `pgrep -P <pane_pid>` — if the shell has no child processes, Claude has exited.

**Grace period:** 5 seconds of consistent "not running" before killing. Prevents false positives during brief process transitions (e.g., Claude restarting after `/clear`).

**Cleanup sequence:**
1. Kill tmux window
2. Unbind thread from topic
3. Clear topic state (status messages, interactive UI)
4. Send notification: "Сессия завершена. Напиши сюда чтобы начать новую или возобновить."

**Resume flow:** After cleanup, user writes in the same topic → directory browser → session picker shows previous sessions → resume with `--resume <session_id>`.

---

### Deepgram Nova-3 Voice Transcription

Replaced OpenAI Whisper (`gpt-4o-transcribe`) with Deepgram Nova-3 REST API.

- Endpoint: `https://api.deepgram.com/v1/listen`
- Model: `nova-3`
- Language: `ru` (Russian)
- Auth: `Token <key>` header (not Bearer)
- Input: raw OGG bytes via `Content-Type: audio/ogg` (simpler than OpenAI's multipart form)
- Output: `results.channels[0].alternatives[0].transcript`
- Error handling: no raw API body in exceptions

**Config:** `DEEPGRAM_API_KEY` in `.env` (optional — voice messages won't work without it)

---

### Full Russian Localization

Every user-facing string translated to Russian:

**Bot commands menu (13 commands):**
- `/start` — Приветствие и справка
- `/history` — История сообщений сессии
- `/screenshot` — Скриншот терминала
- `/esc` — Прервать Claude (Escape)
- `/kill` — Завершить сессию и удалить топик
- `/unbind` — Отвязать топик (окно останется)
- `/usage` — Остаток лимита Claude Code
- `/clear` — Очистить историю диалога
- `/compact` — Сжать контекст разговора
- `/cost` — Показать расход токенов
- `/help` — Справка Claude Code
- `/memory` — Редактировать CLAUDE.md
- `/model` — Сменить модель ИИ

**UI elements:**
- Directory browser: "Выбор рабочей папки", "Нажми на папку для входа", кнопки "Выбрать"/"Отмена"
- Window picker: "Привязать к окну", "Новая сессия"/"Отмена"
- Session picker: "Возобновить сессию?", "Найдены существующие сессии", "Новая сессия"/"Отмена"

**Error messages:** All translated — "Нет доступа", "Нет привязанной сессии", "Окно больше не существует", "Слишком много сообщений", "Эта команда работает только в топике", etc.

**Callback answers:** "Создано"/"Ошибка", "Сессия не найдена", "Список окон изменился", "Окно не найдено", etc.

---

### Log Rotation

- `RotatingFileHandler` at `~/.ccbot/ccbot.log`
- Max size: 1MB per file
- Backup count: 3 (keeps `ccbot.log`, `ccbot.log.1`, `ccbot.log.2`, `ccbot.log.3`)
- Encoding: UTF-8
- Permissions: `chmod 600` (owner-only)
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

---

### Stale Thread Auto-Unbind

When Telegram returns `BadRequest("Message thread not found")` or `BadRequest("Thread not found")`:
- Thread automatically unbound from session
- Uses **exact string match** (not substring) to prevent false positives from future API error message changes
- Prevents infinite error loops that previously spammed logs

---

### Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `CCBOT_CLEAN_OUTPUT` | `true` | Filter tool spam from Telegram, show only responses |
| `CCBOT_ALLOWED_ROOTS` | `~` | Comma-separated directory boundaries for browser + file sending |
| `CCBOT_SHOW_USER_MESSAGES` | `false` | Echo user messages back with 👤 prefix |
| `DEEPGRAM_API_KEY` | — | Deepgram API key for voice transcription (optional) |

### Files Modified

| File | Changes |
|------|---------|
| `src/ccbot/bot.py` | +351 lines: reactions, rate limiting, file sending, TG phrases, Russian UI, clean output handling |
| `src/ccbot/session_monitor.py` | +120 lines: clean output filtering, file detection, working status, dedup |
| `src/ccbot/config.py` | +34 lines: clean_output, allowed_roots, deepgram_api_key, SENSITIVE_ENV_VARS |
| `src/ccbot/tmux_manager.py` | +45 lines: is_claude_running (pgrep), input sanitization, audit logging |
| `src/ccbot/handlers/directory_browser.py` | +41 lines: Russian UI, allowed_roots boundary, session picker translation |
| `src/ccbot/handlers/status_polling.py` | +38 lines: auto-cleanup with grace period |
| `src/ccbot/transcribe.py` | +32 lines: Deepgram Nova-3 API |
| `src/ccbot/handlers/message_queue.py` | +19 lines: BadRequest handling, thread tracking |
| `src/ccbot/main.py` | +18 lines: RotatingFileHandler, log permissions, message_reaction |
| `src/ccbot/handlers/message_sender.py` | +15 lines: BadRequest re-raise for thread errors |
| `src/ccbot/session.py` | +6 lines: message length limit, Russian error |
| `tests/ccbot/test_transcribe.py` | +67 lines: Deepgram response format tests |
