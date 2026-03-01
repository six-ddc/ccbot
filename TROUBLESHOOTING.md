# Troubleshooting Guide

## Session Not Being Tracked

### Symptom: "No active users for session" in logs

This means ccbot is tracking the session but can't find users to deliver messages to. Check:

1. **session_map.json format** - Must be object format, not string:
   ```json
   {
     "ccbot:@8": {
       "session_id": "uuid-here",
       "cwd": "/home/ubuntu",
       "window_name": "ubuntu"
     }
   }
   ```

   **Wrong format (string value):**
   ```json
   {
     "ccbot:@8": "uuid-here"
   }
   ```

2. **state.json window_states must have cwd populated:**
   ```json
   {
     "window_states": {
       "@8": {
         "session_id": "uuid-here",
         "cwd": "/home/ubuntu",  ← MUST be set
         "window_name": "ubuntu"
       }
     }
   }
   ```

   The `resolve_session_for_window()` function returns `None` when `cwd` is empty.

### How to Fix

1. Find the correct session ID:
   ```bash
   ls -lt ~/.claude/projects/-home-ubuntu/*.jsonl | head -5
   ```

2. Update session_map.json with correct format:
   ```bash
   nano ~/.ccbot/session_map.json
   ```

3. Restart ccbot:
   ```bash
   killall ccbot && ccbot run &
   ```

## Status Messages "Brewing" / "Forging"

These are from `status_polling.py` which polls the terminal status line every 1 second.

To disable: Edit `src/ccbot/bot.py` line 1545-1546 and comment out:
```python
# _status_poll_task = asyncio.create_task(status_poll_loop(application.bot))
# logger.info("Status polling task started")
logger.info("Status polling DISABLED")
```

## Thinking Messages in Telegram

Internal Claude thinking blocks have `content_type="thinking"`. The fix in `handle_new_message()` skips these:

```python
if msg.content_type == "thinking":
    logger.debug(f"Skipping thinking message for session {msg.session_id}")
    return
```

## Wrong Session Tracked

If ccbot is tracking the wrong session:
1. Check which session is active in your tmux window
2. Find the session file with the actual content you're working on
3. Update session_map.json with the correct session_id
4. Clear monitor_state.json: `echo '{"tracked_sessions": {}}' > ~/.ccbot/monitor_state.json`
5. Restart ccbot
