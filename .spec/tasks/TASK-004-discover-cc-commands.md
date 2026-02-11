---
id: TASK-004
title: Implement CC command discovery and menu registration
status: done
req: REQ-002
epic: EPIC-002
---

# TASK-004: Implement CC command discovery and menu registration

Auto-discover Claude Code commands from filesystem and register them in the Telegram bot menu.

## Implementation Steps

1. Create `cc_commands.py` module with:
   - `CCCommand` dataclass: `name`, `description`, `source` (builtin/skill/command)
   - `discover_cc_commands() -> list[CCCommand]`: scans builtins, `~/.claude/skills/`, `~/.claude/commands/`
   - YAML frontmatter parser for skill/command files
2. Create `register_commands()` function:
   - 4 bot-native commands first
   - Up to 46 discovered commands (Telegram limit: 50 total)
   - Truncate descriptions to 256 chars
   - Call `bot.set_my_commands()`
3. Call `register_commands()` on startup
4. Schedule periodic refresh (every 10 minutes) via `application.job_queue`
5. Remove hardcoded `CC_COMMANDS` dict from `bot.py`

## Acceptance Criteria

- CC builtins (clear, compact, cost, help, memory) discovered
- Skills with `user-invocable: true` frontmatter discovered
- Custom commands from `~/.claude/commands/` discovered
- Menu registered at startup and refreshed every 10 minutes
- `forward_command_handler` works with discovered commands
- Tests mock filesystem to verify discovery logic
