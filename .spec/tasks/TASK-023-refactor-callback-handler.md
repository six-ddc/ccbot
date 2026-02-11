---
id: TASK-023
title: Refactor callback_handler into dispatch map + modules
status: done
priority: high
req: REQ-006
epic: EPIC-006
depends: [TASK-022]
---

# TASK-023: Refactor callback_handler into dispatch map + modules

Split the monolithic `callback_handler` (bot.py:707, 200+ lines) into a prefix-based dispatch map and dedicated callback modules.

## Implementation Steps

1. Add characterization tests for current callback routing behavior
2. Create dispatch map: `dict[str, Callable]` mapping CB\_\* prefixes to handler functions
3. Extract callback groups into dedicated modules under `handlers/`:
   - `handlers/recovery_callbacks.py` — CB*RECOVERY*\* handlers
   - `handlers/directory_callbacks.py` — CB*DIR*\* handlers (already partly in directory_browser.py)
   - `handlers/window_callbacks.py` — CB*WIN*\* handlers
   - `handlers/screenshot_callbacks.py` — CB*SCREENSHOT*_, CB*KEYS*_, CB*STATUS*\* handlers
4. Keep `callback_handler` in bot.py as thin dispatcher: parse prefix → lookup → call
5. Move shared validation patterns (thread mismatch, user ownership) into reusable helpers
6. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- `callback_handler` is < 30 lines (dispatch only)
- Each callback module is self-contained with its own tests
- All existing callback tests still pass
- New characterization tests cover routing for all CB\_\* prefixes
