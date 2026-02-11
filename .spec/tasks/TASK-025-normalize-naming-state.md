---
id: TASK-025
title: Normalize naming and centralize state handling
status: todo
priority: normal
req: REQ-006
epic: EPIC-006
depends: [TASK-024]
---

# TASK-025: Normalize naming and centralize state handling

Replace short aliases in core flows and centralize stringly-typed user-data keys.

## Implementation Steps

1. **Naming normalization** in core flows:
   - `wid` → `window_id`
   - `ws` → `window_state`
   - `w` → `window`
   - `uid` / `user.id` aliases → `user_id`
   - `tid` → `thread_id`
   - Keep short names in tight scopes (loop vars, list comprehensions)
2. **Centralize user-data keys**:
   - Create `handlers/user_state.py` with constants:
     `PENDING_THREAD_ID`, `PENDING_THREAD_TEXT`, `RECOVERY_WINDOW_ID`, etc.
   - Or use a typed dataclass/TypedDict for user_data access
   - Replace all bare string keys (`"_pending_thread_id"`, etc.)
3. **JSON boundary mapping**:
   - Keep external camelCase keys only at JSON parse boundaries
   - Immediately map to snake_case internal models
4. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- No short aliases (`wid`, `ws`, `w`) in functions > 10 lines
- All user-data keys defined as constants in one module
- Zero bare string keys for user_data access
- All tests pass unchanged (rename is mechanical)
