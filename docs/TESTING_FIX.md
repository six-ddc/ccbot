# Testing the Session Map Race Condition Fix

## Problem
When creating a new Telegram topic and using the window picker to select an existing tmux window, ccbot would bind the thread before the SessionStart hook had time to write the session_map.json entry. This caused "Session not in session_map" errors.

## Fix
Added `wait_for_session_map_entry()` call in the window picker callback handler (bot.py line ~1141) to ensure the SessionStart hook completes before binding.

## Testing Procedure

### 1. Install from Fork
```bash
# Uninstall existing ccbot
pipx uninstall ccbot

# Install from fork with editable mode (for testing)
cd ~/ccbot-fork
pipx install .
```

### 2. Restart ccbot
```bash
cd ~/ccbot-fork
./scripts/restart.sh
```

### 3. Test Window Picker Flow

#### Scenario A: Select existing window (NEW TOPIC)
1. Create a NEW topic in Telegram (not previously bound)
2. Send any message to the new topic
3. ccbot should respond with window picker UI showing available windows
4. Select a window (e.g., "ubuntu")
5. **Expected behavior**:
   - No error about "Session not in session_map"
   - Topic binds successfully to the window
   - Topic renames to match window name
   - Pending message (if any) is forwarded to the window

#### Scenario B: Auto-create new session (no existing windows)
1. Create a NEW topic in Telegram
2. Kill all existing tmux windows (except ccbot's own): `tmux kill-window -t @8`
3. Send any message to the new topic
4. ccbot should offer directory browser for creating new window
5. **Expected behavior**: Works as before (this flow wasn't broken)

### 4. Verify session_map.json
```bash
cat ~/.ccbot/session_map.json
```

Should contain entries for all bound windows, including newly bound ones.

### 5. Check logs for any warnings
```bash
journalctl -u ccbot -n 50 --no-pager
```

Should NOT see:
- "Session map entry not found for window"
- "Session not ready" errors (unless genuine timeout)

## Success Criteria
✅ Window picker successfully binds to existing windows
✅ No "Session not in session_map" errors
✅ Topic renames to match window name
✅ Pending messages are forwarded correctly
✅ session_map.json contains the new binding

## Rollback
If issues occur:
```bash
cd ~/ccbot-fork
git revert HEAD
./scripts/restart.sh
```

Or uninstall from fork and reinstall from PyPI:
```bash
pipx uninstall ccbot
pipx install ccbot
```
