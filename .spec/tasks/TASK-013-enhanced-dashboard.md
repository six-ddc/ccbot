---
id: TASK-013
title: Enhanced /sessions dashboard with actions
status: done
req: REQ-004
epic: EPIC-004
depends: [TASK-006, TASK-007]
---

# TASK-013: Enhanced /sessions dashboard with actions

Upgrade the basic sessions dashboard with expandable details and per-session actions.

## Implementation Steps

1. Add per-session expandable details (tap session → show cwd, session ID, uptime)
2. Add per-session action buttons: `[Screenshot]`, `[Esc]`, `[Kill]`
3. Kill confirmation with two-step dialog
4. Pin dashboard message in General topic
5. Auto-refresh on significant state changes

## Acceptance Criteria

- Session details expandable per session
- Per-session Screenshot/Esc/Kill actions work
- Kill has confirmation step
- Dashboard can be pinned
