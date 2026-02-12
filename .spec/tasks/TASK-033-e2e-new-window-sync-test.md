---
id: TASK-033
title: End-to-end test for manual tmux window sync
status: in_progress
priority: medium
epic: EPIC-001
depends: [TASK-032]
---

# TASK-033: End-to-end test for manual tmux window sync

Integration test covering the full flow of detecting a new tmux window and syncing
it to a Telegram forum topic: session_map update -> monitor detection ->
\_handle_new_window callback -> topic creation -> binding verification.

## Implementation Steps

1. Create `tests/ccbot/test_new_window_sync.py` with integration tests.
2. Test the monitor -> callback -> topic creation -> binding chain end-to-end.
3. Cover both normal (existing bindings) and cold-start (CCBOT_GROUP_ID) paths.
4. Verify binding state after the full flow completes.

## Acceptance Criteria

- Integration test validates full session_map -> monitor -> topic -> binding flow
- Tests cover: with existing bindings, cold-start with CCBOT_GROUP_ID, cold-start without
- Binding state verified after flow completion
- All tests pass: `make check`
