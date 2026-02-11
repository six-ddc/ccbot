# CLAUDE.md

ccmux — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows. Each topic is bound to one tmux window running one Claude Code instance.

Tech stack: Python, python-telegram-bot, tmux, uv.

## Common Commands

```bash
make check                            # Run all: fmt, lint, typecheck, test
make fmt                              # Format code
make lint                             # Lint — MUST pass before committing
make typecheck                        # Type check — MUST be 0 errors before committing
make test                             # Run test suite
./scripts/restart.sh                  # Restart the ccbot service after code changes
ccbot hook --install                  # Auto-install Claude Code SessionStart hook
```

## Core Design Constraints

- **1 Topic = 1 Window = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), not window name. Window names kept as display names. Same directory can have multiple windows.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json`; monitor polls it to detect session changes.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — 1.1s minimum interval between messages per user via `rate_limit_send()`.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets).
- Project structure: handlers in `src/ccbot/handlers/`, core modules in `src/ccbot/`, tests mirror source under `tests/ccbot/`.

## Hook Configuration

Auto-install: `ccbot hook --install`

Or manually in `~/.claude/settings.json`:

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

## Spec-Driven Development

Task management via `.spec/` directory. One task per session — complete fully before starting another.

```
.spec/
├── reqs/     # REQ-*.md (WHAT — requirements, success criteria)
├── epics/    # EPIC-*.md (grouping)
├── tasks/    # TASK-*.md (HOW — implementation steps)
├── memory/   # conventions.md, decisions.md
└── SESSION.yaml
```

| Command        | Purpose                         |
| -------------- | ------------------------------- |
| `/spec:work`   | Select, plan, implement, verify |
| `/spec:status` | Progress overview               |
| `/spec:new`    | Create new task or requirement  |
| `/spec:done`   | Mark complete with evidence     |

**Quick queries** (`~/.claude/scripts/specctl`):

```bash
specctl status                # Progress overview
specctl ready                 # Next tasks (priority-ordered)
specctl session show          # Current session state
specctl validate              # Check for issues
```

Never mark done until: `make check` passes (fmt + lint + typecheck + test).

## Architecture Details

See @.claude/rules/architecture.md for full system diagram and module inventory.
See @.claude/rules/topic-architecture.md for topic→window→session mapping details.
See @.claude/rules/message-handling.md for message queue, merging, and rate limiting.
