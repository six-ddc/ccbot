---
id: EPIC-001
title: "Phase 1: Multi-Instance"
reqs: [REQ-001]
tasks: [TASK-001, TASK-002, TASK-003]
status: done
---

# EPIC-001: Multi-Instance

Foundation phase. Add group filtering so multiple ccbot instances can share one bot token.

## Tasks

- TASK-001: Add config variables (`CCBOT_GROUP_ID`, `CCBOT_INSTANCE_NAME`)
- TASK-002: Add `_is_my_group()` filter to all handlers
- TASK-003: Update docs and `.env.example`
