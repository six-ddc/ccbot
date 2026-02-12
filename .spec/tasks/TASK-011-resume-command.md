---
id: TASK-011
title: Implement /resume command
status: done
req: REQ-003
epic: EPIC-003
depends: [TASK-010]
---

# TASK-011: Implement /resume command

Explicit `/resume` command for browsing and resuming any past session.

## Implementation Steps

1. Register `CommandHandler("resume", resume_command)` in `bot.py`
2. Scan `~/.claude/projects/` for all recent sessions across all project dirs
3. Group sessions by cwd
4. Show paginated inline keyboard:
   ```
   /path/to/project-a:
     [12:30 — "Fix auth bug"]
     [Yesterday — "Add notifications"]
   /path/to/project-b:
     [Feb 9 — "Refactor queries"]
   ```
5. On selection: create window with `claude --resume <id>`, bind to current topic

## Acceptance Criteria

- `/resume` shows recent sessions grouped by project directory
- Session descriptions include timestamp and summary
- Selection creates window with `--resume` and binds topic
- Works in both bound and unbound topics
