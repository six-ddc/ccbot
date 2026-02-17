---
id: EPIC-002
title: "Phase 2: Command Overhaul"
reqs: [REQ-002]
tasks: [TASK-004, TASK-005, TASK-006, TASK-007, TASK-008]
status: done
---

# EPIC-002: Command Overhaul

Reorganize commands into bot-native, auto-discovered CC, and contextual inline actions.

## Tasks

- TASK-004: Implement `discover_cc_commands()` and dynamic menu registration
- TASK-005: Rename `/start` to `/new`
- TASK-006: Add `/sessions` dashboard command (basic version)
- TASK-007: Demote `/esc`, `/kill`, `/screenshot` to inline buttons
- TASK-008: Update `forward_command_handler` for discovered commands
