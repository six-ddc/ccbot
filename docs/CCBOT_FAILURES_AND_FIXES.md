# ccbot Failure Analysis and Fixes

This document catalogs the reasons ccbot has failed, the fixes applied, and lessons learned for future debugging and maintenance.

## Table of Contents
- [Rate Limiting Issues](#rate-limiting-issues)
- [Session Tracking Problems](#session-tracking-problems)
- [Token Swapping Procedure](#token-swapping-procedure)
- [Historical Fixes](#historical-fixes)
- [Prevention Strategies](#prevention-strategies)

---

## Rate Limiting Issues

### Problem: Telegram API Rate Limit Blocking

**Symptoms:**
```
telegram.ext.AIORateLimiter - INFO - Rate limit hit. Retrying after 37470.100000 seconds
```
- Bot appears online but doesn't respond to any messages
- Existing sessions stop receiving updates
- Messages sent to the bot are not delivered
- Rate limit can last 10+ hours

**Root Cause:**
The Telegram Bot API has rate limits on bot API calls. When a bot sends too many requests too quickly (especially sending a large response in many small chunks), it hits the rate limit and gets temporarily blocked.

The rate limit is **per bot token** - other bots and Telegram services are unaffected.

**Detection:**
```bash
# Check logs for rate limit errors
pm2 logs ccbot --lines 100 | grep -i "rate limit"

# Check if bot is actually running
pm2 status ccbot
ps aux | grep ccbot
```

**Fix Applied: Token Swapping**

When rate limited, swap to a new bot token:

1. **Create a new bot on Telegram:**
   - Message `@BotFather`
   - Send `/newbot`
   - Follow prompts to name your bot
   - Copy the new token

2. **Update the configuration:**
   ```bash
   # Update the .env file with new token
   nano ~/.ccbot/.env
   # Replace TELEGRAM_BOT_TOKEN=...

   # Or use automated edit
   sed -i 's/TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=your_new_token/' ~/.ccbot/.env
   ```

3. **Clear thread bindings** (critical step):
   ```bash
   # Edit state.json to clear old thread bindings
   nano ~/.ccbot/state.json
   # Change "thread_bindings" section to: "thread_bindings": {}
   ```

4. **Restart ccbot:**
   ```bash
   pm2 restart ccbot
   ```

5. **Reinitialize in Telegram:**
   - Find your new bot on Telegram
   - Send `/start` to each topic
   - Create new topics/sessions as needed

**Why Clearing Thread Bindings is Necessary:**

Thread bindings contain Telegram message thread IDs that are **specific to each bot token**. When you swap tokens:
- Old thread IDs don't exist for the new bot
- Bot tries to send messages to non-existent threads
- Results in "Message thread not found" errors

By clearing `thread_bindings` in `state.json`, the bot can create fresh bindings for the new token.

---

## Session Tracking Problems

### Problem: Session Map Race Condition

**Symptoms:**
- "Session not in session_map" errors when binding new topics
- Window picker fails to bind to existing windows
- Session monitor can't find newly created sessions

**Root Cause:**
When creating a new Telegram topic and using the window picker to select an existing tmux window, ccbot would bind the thread **before** the SessionStart hook had time to write the session_map.json entry.

**Fix Applied:**
Added `wait_for_session_map_entry()` call in the window picker callback handler (`bot.py` line ~1141) to ensure the SessionStart hook completes before binding.

**Testing:**
See `docs/TESTING_FIX.md` for complete testing procedure.

### Problem: Wrong Session ID Format

**Symptoms:**
```
WARNING ccbot.hook: Invalid session_id format: test-123
```

**Root Cause:**
The hook validates session IDs to ensure they're proper UUIDs. Test or malformed session IDs are rejected.

**Fix:**
Ensure session IDs are proper UUIDs (e.g., `5bdaf9e8-0f61-452b-9894-1f4b611f1c1a`)

### Problem: Session Not Being Tracked

**Symptoms:**
- "No active users for session" in logs
- Messages not reaching Telegram

**Root Causes:**

1. **Incorrect session_map.json format:**

   **Wrong (string value):**
   ```json
   {
     "ccbot:@8": "uuid-here"
   }
   ```

   **Correct (object format):**
   ```json
   {
     "ccbot:@8": {
       "session_id": "uuid-here",
       "cwd": "/home/ubuntu",
       "window_name": "ubuntu"
     }
   }
   ```

2. **Missing cwd in state.json:**
   ```json
   {
     "window_states": {
       "@8": {
         "session_id": "uuid-here",
         "cwd": "/home/ubuntu",  // MUST be set
         "window_name": "ubuntu"
       }
     }
   }
   ```

   The `resolve_session_for_window()` function returns `None` when `cwd` is empty.

**Fix:**
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
   pm2 restart ccbot
   ```

---

## Token Swapping Procedure

Complete token swap workflow for when rate limiting occurs:

```bash
#!/bin/bash
# Token swap procedure for ccbot

# 1. Get new token from @BotFather on Telegram
NEW_TOKEN="your_new_token_here"

# 2. Backup current state
cp ~/.ccbot/.env ~/.ccbot/.env.backup
cp ~/.ccbot/state.json ~/.ccbot/state.json.backup

# 3. Update token in .env
sed -i "s/TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=$NEW_TOKEN/" ~/.ccbot/.env

# 4. Clear thread bindings from state.json
python3 << 'EOF'
import json
with open('/home/ubuntu/.ccbot/state.json', 'r') as f:
    state = json.load(f)
state['thread_bindings'] = {}
with open('/home/ubuntu/.ccbot/state.json', 'w') as f:
    json.dump(state, f, indent=2)
EOF

# 5. Restart ccbot
pm2 restart ccbot

echo "Token swapped. Please send /start to your new bot on Telegram."
```

---

## Historical Fixes

### 1. Startup Cleanup
**Issue:** Tracked sessions persisting after being closed
**Fix:** Added cleanup on startup to remove sessions not present in session_map
**Commit:** `3178d75`

### 2. ANSI Escape Code Stripping
**Issue:** ANSI codes appearing in Telegram messages
**Fix:** Strip ANSI escape codes from parsed message text
**Commit:** `70183a0`

### 3. Corrupted Byte Offset Recovery
**Issue:** Monitor state corrupted causing read failures
**Fix:** Auto-reset byte offset when file size is smaller than stored offset
**Commit:** `c769cc0`

### 4. Status Polling Disable Option
**Issue:** "Brewing"/"Forging" status messages flooding chat
**Fix:** Made status polling optional, skip thinking messages
**Commit:** `09b463f`

### 5. Session Map Cleanup
**Issue:** Stale entries in session_map.json
**Fix:** Clean up stale session_map entries on startup
**Commit:** `3178d75`

### 6. Window ID Resolution
**Issue:** Using find_window_by_name causing incorrect window matching
**Fix:** Use find_window_by_id instead in usage_command
**Commit:** `2b99b8c`

---

## Prevention Strategies

### Rate Limiting Prevention

1. **Batch Messages:** Send fewer, larger messages instead of many small ones
2. **Adjust Rate Limits:** Configure `AIORateLimiter` settings in `bot.py`
3. **Monitor Usage:** Track message frequency to stay under limits
4. **Queue Management:** Ensure message queue doesn't flood

### Session Tracking Best Practices

1. **Always use the hook:** Don't manually edit session_map.json unless necessary
2. **Validate format:** Ensure session_map.json uses object format, not strings
3. **Check cwd:** Verify state.json window_states have cwd populated
4. **Clean restart:** Use proper restart procedure (pm2 restart) to ensure clean state

### Monitoring and Debugging

**Key log locations:**
```bash
# PM2 logs
~/.pm2/logs/ccbot-out.log
~/.pm2/logs/ccbot-error.log

# ccbot logs
~/.ccbot/ccbot.log
~/.ccbot/ccbot.out
```

**Health check script:**
```bash
#!/bin/bash
echo "=== ccbot Health Check ==="
echo ""

# Check if running
pm2 status ccbot | grep online && echo "✓ ccbot is running" || echo "✗ ccbot is NOT running"

# Check for rate limits
echo ""
echo "=== Rate Limit Check ==="
tail -100 ~/.pm2/logs/ccbot-error.log | grep -i "rate limit" && echo "✗ RATE LIMITED" || echo "✓ No rate limit issues"

# Check for thread errors
echo ""
echo "=== Thread Binding Check ==="
tail -100 ~/.pm2/logs/ccbot-error.log | grep -i "thread not found" && echo "✗ Thread binding errors" || echo "✓ No thread binding errors"

# Check session map
echo ""
echo "=== Session Map Status ==="
cat ~/.ccbot/session_map.json | python3 -m json.tool > /dev/null 2>&1 && echo "✓ session_map.json valid" || echo "✗ session_map.json INVALID"
```

### State File Management

**Backup before changes:**
```bash
# Create backup
cp ~/.ccbot/state.json ~/.ccbot/state.json.backup.$(date +%Y%m%d_%H%M%S)

# Restore if needed
cp ~/.ccbot/state.json.backup.YYYYMMDD_HHMMSS ~/.ccbot/state.json
pm2 restart ccbot
```

---

## Quick Reference: Common Issues

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Bot not responding | Rate limited | Swap token |
| "Thread not found" | Wrong token | Swap token + clear bindings |
| "Session not in session_map" | Race condition | Wait for hook (fixed in latest) |
| No messages in Telegram | No active users | Check session_map format |
| Wrong session tracked | Bad session_id | Update session_map.json |
| "Session not ready" | Hook not fired | Check hook installation |

---

## Contributing

When adding new fixes:
1. Document the issue with clear symptoms
2. Identify the root cause
3. Document the fix with code references
4. Add testing procedures
5. Update this document

## Related Documentation

- `TROUBLESHOOTING.md` - General troubleshooting guide
- `docs/TESTING_FIX.md` - Testing procedures for specific fixes
- `.claude/rules/architecture.md` - System architecture
- `.claude/rules/topic-architecture.md` - Topic mapping details
