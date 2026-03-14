# Security Policy

## Reporting Vulnerabilities

Open a private GitHub issue or email the maintainer directly. Do not disclose security vulnerabilities in public issues. Include:

- A description of the vulnerability and affected component
- Steps to reproduce or a proof-of-concept
- Potential impact assessment

Patches are expected within 14 days for critical issues.

## Security Model

CCBot is a single-user or small-group tool. It runs on a trusted local machine alongside tmux and Claude Code.

**CCBot trusts:**
- The local filesystem and tmux server (same machine, same user).
- Telegram's transport layer (TLS) for message delivery.
- Claude Code's JSONL output format (read-only, no execution of JSONL content).

**CCBot does not trust:**
- Any Telegram user not listed in `ALLOWED_USERS`.
- Content of user messages (sanitized before passing to tmux).
- File paths extracted from Claude Code output (re-validated before sending).
- The `session_map.json` file (session IDs validated against UUID regex before use).

## Threat Model

| Adversary | Vector | Mitigation |
|-----------|--------|------------|
| Unauthorized Telegram user | Messages from unknown user ID | `ALLOWED_USERS` whitelist checked on every handler |
| Rate abuse by allowed user | Message flood to exhaust Claude / tmux | Per-user rate limit: 30 msg/60s |
| tmux injection via message text | Null bytes, carriage returns in `send_keys` | Strip `\x00` and `\r` before send; printable-only filter |
| Command injection via session ID | Malicious `session_map.json` or user-supplied resume ID | UUID regex validation + `shlex.quote()` |
| Directory traversal via browser | `..` navigation in directory picker | `Path.is_relative_to(allowed_roots)` at navigation and at confirm |
| Race condition in directory confirm | Navigate up then immediately confirm | Path re-validated in `CB_DIR_CONFIRM` callback, not cached from navigation |
| Sensitive file exfiltration via Write tool | Claude writes `.env`, SSH keys, state files; bot auto-sends | Resolved path checked: allowed_roots, sensitive name blocklist, config dir exclusion |
| Secret leakage to Claude subprocess | Env vars inherited via tmux | `SENSITIVE_ENV_VARS` scrubbed from `os.environ` and tmux session env at startup |
| Log file exposure | World-readable log containing tokens/paths | Log file created with `chmod 600` (owner-only) |
| Token exposure in logs | Bot token logged in full | Token redacted in all log output (4 chars + `****`) |
| API key leakage in error messages | Deepgram raw response body in exceptions | Exception messages do not include raw API response body |
| Stale session re-attachment | Old `session_map.json` entries from crashed session | Startup cleanup removes all tracked sessions not present in current map |
| Memory exhaustion via thread map | Unbounded `_msg_thread_map` growth | Hard cap at 10k entries; TTL eviction (14 days); forced eviction to 5k on overflow |
| Concurrent hook races | Multiple `SessionStart` hooks writing `session_map.json` simultaneously | `fcntl.LOCK_EX` file lock on `session_map.lock` during read-modify-write |

## Hardening Measures

| Category | Measure | Location |
|----------|---------|----------|
| Authentication | `ALLOWED_USERS` whitelist; all handlers reject unknown user IDs silently | `config.py`, `bot.py` |
| Rate limiting | 30 messages / 60s per user; cleanup every 50 calls (O(1) amortized) | `bot.py` |
| Input sanitization | Strip null bytes (`\x00`) and carriage returns (`\r`); reject non-printable (< 0x20, not newline) | `tmux_manager.send_keys()` |
| Length enforcement | Single 4096-char limit applied in `session.send_to_window()` before any handler | `session.py` |
| Injection prevention | UUID regex on `--resume` session IDs; `shlex.quote()` for shell interpolation | `tmux_manager.create_window()` |
| Directory boundary | `CCBOT_ALLOWED_ROOTS` (default: `~`); `Path.is_relative_to()` at navigation step and confirm callback | `handlers/directory_browser.py` |
| File send restrictions | Allowed extensions allowlist (`.md .txt .pdf .docx .doc .xlsx .xls .csv .html .htm .rtf .epub`); sensitive name blocklist; config dir excluded; `.json`/`.xml` excluded; 0 < size < 50MB; dedup by resolved path | `session_monitor.py`, `bot.py` |
| Sensitive name blocklist | `.env`, `credentials`, `id_rsa`, `id_ed25519`, `id_dsa`, `.netrc`, `.pgpass`, `session_map.json`, `state.json`, `monitor_state.json` | `session_monitor.py` |
| Secret isolation | `SENSITIVE_ENV_VARS` removed from `os.environ` and tmux session env after capture | `config.py`, `tmux_manager.py` |
| Log security | `RotatingFileHandler` with `chmod 600`; token redacted in all log lines | `main.py` |
| API error handling | Deepgram error responses do not expose raw body in raised exceptions | `transcribe.py` |
| File lock | `fcntl.LOCK_EX` exclusive lock prevents concurrent hook writes to `session_map.json` | `hook.py` |
| Hook input validation | `session_id` validated against UUID regex; `cwd` must be an absolute path | `hook.py` |
| Session ID validation | UUID format enforced at hook write time and at `--resume` command construction | `hook.py`, `tmux_manager.py` |
| Process detection | `pgrep -P <pane_pid>` checks shell child processes — cannot be spoofed by process name | `tmux_manager.is_claude_running()` |
| Grace period | 5s consistent "not running" before auto-kill — prevents false kills during restarts | `handlers/status_polling.py` |
| Stale thread unbind | `BadRequest("thread not found")` uses exact string match to unbind — no false positives | `handlers/message_queue.py` |
| Memory cap | `_msg_thread_map` capped at 10k entries with 14-day TTL; forced eviction to 5k on overflow | `bot.py` |

## Known Limitations

**Single user-level boundary.** All security relies on Telegram user IDs. If an attacker gains access to an allowed user's Telegram account, they have full CCBot access.

**No tmux socket isolation.** CCBot connects to the default tmux socket (`/tmp/tmux-<uid>/default`). Any local process running as the same OS user can attach to the same tmux session and observe or inject input.

**JSONL files are not integrity-checked.** CCBot reads Claude Code's JSONL output and trusts its structure. A maliciously crafted JSONL file on disk (e.g., via a compromised Claude plugin) could inject arbitrary text to Telegram users, but cannot achieve code execution through CCBot.

**`session_map.json` is not signed.** A local attacker (same user) could modify `session_map.json` to redirect a session to an attacker-controlled JSONL file. UUID validation limits injection to valid-format UUIDs.

**Symlinks are followed.** File path resolution follows symlinks before checking `allowed_roots`. A symlink inside an allowed root that points outside can pass the boundary check if the real target is not outside an allowed root — however, `Path.resolve()` resolves the final target, so the check is performed on the real path, not the symlink path.

**No end-to-end encryption of JSONL.** Claude Code session transcripts are stored in plaintext at `~/.claude/projects/`. Disk encryption is recommended for sensitive workloads.

**Rate limit is in-memory.** Restarting the bot resets per-user rate limit counters. A user can send a burst immediately after a bot restart.

**Telegram bot token grants full bot access.** Anyone with the `TELEGRAM_BOT_TOKEN` value can impersonate the bot. The token is scrubbed from the process environment at startup but exists in the `.env` file. Protect `.env` with filesystem permissions.
