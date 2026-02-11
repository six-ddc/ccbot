---
id: TASK-005
title: Rename /start to /new
status: done
req: REQ-002
epic: EPIC-002
---

# TASK-005: Rename /start to /new

Rename the `/start` command to `/new` for clarity. `/start` still works as alias temporarily.

## Implementation Steps

1. Rename `start_command` function to `new_command` in `bot.py`
2. Register as `CommandHandler("new", new_command)`
3. Optionally keep `CommandHandler("start", new_command)` as temporary alias
4. Update menu description: "Create new Claude session"
5. Update any references in tests and docs

## Acceptance Criteria

- `/new` creates a new session (same behavior as old `/start`)
- `/start` still works (temporary compatibility)
- Tests updated
