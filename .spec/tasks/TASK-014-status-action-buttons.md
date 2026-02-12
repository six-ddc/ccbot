---
id: TASK-014
title: Quick action buttons on status messages
status: done
req: REQ-004
epic: EPIC-004
depends: [TASK-007]
---

# TASK-014: Quick action buttons on status messages

Add contextual inline buttons to status messages and remove them when status clears.

## Implementation Steps

1. In `status_polling.py` / `message_queue.py`, when sending status messages:
   - Add `[Esc]` and `[Screenshot]` inline buttons
2. When status clears (spinner stops):
   - Edit message to remove inline keyboard
3. For error/failure messages: add `[Retry]` and `[Screenshot]` buttons
4. For session expired messages: add `[Fresh]`, `[Continue]`, `[Resume]` buttons

## Acceptance Criteria

- Active status messages have Esc and Screenshot buttons
- Buttons removed when status clears
- Error messages have Retry button
- Buttons are responsive and functional
