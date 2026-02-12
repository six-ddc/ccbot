---
id: TASK-026
title: Harden exception strategy and logging
status: done
priority: normal
req: REQ-006
epic: EPIC-006
depends: [TASK-025]
---

# TASK-026: Harden exception strategy and logging

Replace broad exception catches with specific exceptions and standardize logging format.

## Implementation Steps

1. **Audit all `except Exception` blocks**:
   - Replace with specific exceptions: `TelegramError`, `OSError`, `json.JSONDecodeError`, etc.
   - Use `contextlib.suppress(SpecificError)` for intentional suppression
   - Remove silent `pass` branches; add `logger.debug` with context
2. **Standardize logging**:
   - Replace all f-string log calls with lazy formatting: `logger.info("msg %s", val)`
   - Ensure structured context in log messages (user_id, window_id, thread_id)
   - Check that no sensitive data (tokens, paths) leaks into logs
3. **Add error recovery patterns**:
   - Graceful degradation for Telegram API errors (retry-after, flood control)
   - Explicit handling for tmux process errors
4. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- Zero `except Exception` without justifying comment
- Zero `except: pass` or `except Exception: pass`
- All log calls use lazy formatting (enforced by Ruff G rule)
- No new behavior changes — error handling is narrowed, not changed
