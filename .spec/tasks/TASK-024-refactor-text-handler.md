---
id: TASK-024
title: Refactor text_handler into explicit steps
status: todo
priority: high
req: REQ-006
epic: EPIC-006
depends: [TASK-023]
---

# TASK-024: Refactor text_handler into explicit steps

Split `text_handler` (bot.py:518) into named, testable steps.

## Implementation Steps

1. Add characterization tests for current text_handler flows:
   - Auth/group check → reject
   - No topic → ignore
   - Unbound topic → window picker / directory browser
   - Dead window → recovery UI
   - Bound topic → forward message
2. Extract each step into a named function:
   - `_check_auth(update)` → user/group validation
   - `_resolve_topic(update)` → thread_id extraction and topic-type check
   - `_handle_unbound_topic(update, context, thread_id, text)` → picker/browser flow
   - `_handle_dead_window(update, context, wid, thread_id, text)` → recovery UI
   - `_forward_message(update, context, wid, text)` → send to window + post-send actions
3. Keep `text_handler` as orchestrator calling steps in sequence
4. Keep `create_bot()` wiring in bot.py; move behavior into focused modules
5. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- `text_handler` is < 30 lines (orchestration only)
- Each step function is independently testable
- All existing text_handler tests still pass
- New characterization tests cover all 5 flows
