---
id: TASK-012
title: Topic emoji status updates
status: done
req: REQ-004
epic: EPIC-004
---

# TASK-012: Topic emoji status updates

Use `editForumTopic` to set topic emoji reflecting session state.

## Implementation Steps

1. Define emoji mapping: Active → custom emoji, Idle → custom emoji, Dead → custom emoji
2. In `status_polling.py`, when status changes, call `editForumTopic(icon_custom_emoji_id=...)`
3. On dead window detection, set dead emoji
4. Graceful degradation: try once at startup, if permission error → disable feature, log once
5. Avoid excessive API calls: only update when state actually changes

## Acceptance Criteria

- Active sessions show working emoji on topic
- Idle sessions show idle emoji
- Dead sessions show dead emoji
- Works silently when bot lacks permission
- State changes don't trigger excessive API calls
