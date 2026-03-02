#!/bin/bash
# ccbot Health Check Script
# Quick diagnostic tool for common ccbot issues

# Don't exit on errors - we want to see all checks
set +e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== ccbot Health Check ==="
echo ""

# Check for multiple ccbot instances (IMPORTANT - causes conflicts)
echo -n "► Checking for multiple ccbot instances... "
# Only count actual Python ccbot processes, not shell/ssh/ccbot-fork processes
CCBOT_PROCS=$(ps aux | grep -E 'python.*ccbot|ccbot.*\.venv/bin/ccbot' | grep -v 'health-check\|grep' | wc -l)
CCBOT_PIDS=$(ps aux | grep -E 'python.*ccbot|ccbot.*\.venv/bin/ccbot' | grep -v 'health-check\|grep' | awk '{print $2}')
if [ "$CCBOT_PROCS" -eq 0 ]; then
    echo -e "${RED}✗ No ccbot instances found${NC}"
    echo "  Fix: Start ccbot in tmux: tmux send-keys -t ccbot:__main__ 'ccbot run' Enter"
elif [ "$CCBOT_PROCS" -gt 1 ]; then
    echo -e "${RED}✗ $CCBOT_PROCS ccbot instances running (CONFLICT!)${NC}"
    echo "  ${YELLOW}This causes 'terminated by other getUpdates request' errors${NC}"
    echo "  Fix: Kill all but one instance:"
    echo "$CCBOT_PIDS" | while read pid; do echo "    kill $pid"; done
else
    echo -e "${GREEN}✓ Exactly 1 ccbot instance running${NC}"
    CCBOT_PID=$(echo "$CCBOT_PIDS" | head -1)
    echo "  PID: $CCBOT_PID"
fi

# Check if running in tmux
echo ""
echo -n "► Checking if ccbot is running in tmux... "
if tmux has-session -t ccbot 2>/dev/null; then
    if tmux capture-pane -t ccbot:__main__ -p 2>/dev/null | grep -q "ccbot\."; then
        echo -e "${GREEN}✓ ccbot running in tmux ccbot:__main__${NC}"
    else
        echo -e "${YELLOW}⚠ tmux session exists but ccbot may not be running in __main__${NC}"
    fi
else
    echo -e "${YELLOW}⚠ No tmux session 'ccbot' found${NC}"
    echo "  (ccbot may be running via PM2 or directly)"
fi

# Check for rate limits (check both PM2 logs and ccbot logs)
echo ""
echo -n "► Checking for rate limit issues... "
if tail -100 ~/.pm2/logs/ccbot-error.log 2>/dev/null | grep -qi "rate limit\|retry after"; then
    echo -e "${RED}✗ RATE LIMITED DETECTED (PM2 logs)${NC}"
    echo "  Action needed: Swap bot token (see docs/CCBOT_FAILURES_AND_FIXES.md)"
    tail -5 ~/.pm2/logs/ccbot-error.log 2>/dev/null | grep -i "rate limit\|retry after" | head -1 | sed 's/^/  /'
elif tail -100 ~/.ccbot/ccbot.log 2>/dev/null | grep -qi "rate limit\|retry after"; then
    echo -e "${RED}✗ RATE LIMITED DETECTED (ccbot logs)${NC}"
    echo "  Action needed: Swap bot token (see docs/CCBOT_FAILURES_AND_FIXES.md)"
    tail -5 ~/.ccbot/ccbot.log 2>/dev/null | grep -i "rate limit\|retry after" | head -1 | sed 's/^/  /'
else
    echo -e "${GREEN}✓ No rate limit issues${NC}"
fi

# Check for recent Telegram bot conflicts (last 5 minutes)
echo ""
echo -n "► Checking for recent Telegram bot conflicts... "
if find ~/.ccbot/ccbot.log -mmin -5 2>/dev/null | grep -q . && tail -100 ~/.ccbot/ccbot.log 2>/dev/null | grep -qi "Conflict.*terminated by other getUpdates"; then
    echo -e "${RED}✗ Recent bot conflict detected${NC}"
    echo "  ${YELLOW}This means multiple bot instances are polling Telegram${NC}"
    echo "  Fix: Ensure only one ccbot instance is running"
else
    echo -e "${GREEN}✓ No recent bot conflicts${NC}"
fi

# Check for thread errors
echo ""
echo -n "► Checking for thread binding errors... "
if tail -100 ~/.pm2/logs/ccbot-error.log 2>/dev/null | grep -q "thread not found"; then
    THREAD_ERRORS=$(tail -100 ~/.pm2/logs/ccbot-error.log 2>/dev/null | grep -c "thread not found" || true)
    echo -e "${YELLOW}⚠ $THREAD_ERRORS thread binding errors in PM2 logs${NC}"
    echo "  This usually means you need to swap bot token or clear thread bindings"
elif tail -100 ~/.ccbot/ccbot.out 2>/dev/null | grep -q "thread not found\|Message thread not found"; then
    echo -e "${YELLOW}⚠ Thread binding errors in ccbot logs${NC}"
    echo "  This usually means you need to swap bot token or clear thread bindings"
else
    echo -e "${GREEN}✓ No thread binding errors${NC}"
fi

# Check session map
echo ""
echo -n "► Checking session_map.json format... "
if cat ~/.ccbot/session_map.json 2>/dev/null | python3 -m json.tool > /dev/null 2>&1; then
    echo -e "${GREEN}✓ session_map.json is valid JSON${NC}"
    # Check for object format vs string format
    if grep -qE '"[^"]+":\s*"[a-f0-9-]{36}"' ~/.ccbot/session_map.json 2>/dev/null; then
        echo -e "  ${YELLOW}⚠ Warning: session_map may use string format (should be object format)${NC}"
    fi
else
    echo -e "${RED}✗ session_map.json is INVALID${NC}"
    echo "  Fix: Check format (see TROUBLESHOOTING.md)"
fi

# Check state.json
echo ""
echo -n "► Checking state.json format... "
if cat ~/.ccbot/state.json 2>/dev/null | python3 -m json.tool > /dev/null 2>&1; then
    echo -e "${GREEN}✓ state.json is valid JSON${NC}"

    # Check if cwd is populated
    if grep -q '"cwd":\s*""' ~/.ccbot/state.json 2>/dev/null; then
        echo -e "  ${YELLOW}⚠ Warning: Some window_states have empty cwd${NC}"
    fi

    # Check thread_bindings for stale IDs
    THREAD_COUNT=$(python3 -c "import json; s=json.load(open('/home/ubuntu/.ccbot/state.json')); print(sum(len(v) for v in s.get('thread_bindings', {}).values()))" 2>/dev/null || echo "0")
    echo "  Thread bindings: $THREAD_COUNT"
else
    echo -e "${RED}✗ state.json is INVALID${NC}"
fi

# Check session files
echo ""
echo -n "► Checking for active session files... "
SESSION_COUNT=$(ls -1 ~/.claude/projects/*/*.jsonl 2>/dev/null | wc -l)
if [ "$SESSION_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Found $SESSION_COUNT session files${NC}"
    echo "  Latest sessions:"
    ls -lt ~/.claude/projects/*/*.jsonl 2>/dev/null | head -3 | awk '{print "    " $NF " (" $6 " " $7 " " $8 ")"}'
else
    echo -e "${YELLOW}⚠ No session files found${NC}"
fi

# Check tmux session
echo ""
echo -n "► Checking tmux session 'ccbot'... "
if tmux has-session -t ccbot 2>/dev/null; then
    echo -e "${GREEN}✓ tmux session exists${NC}"
    WINDOW_COUNT=$(tmux list-windows -t ccbot 2>/dev/null | wc -l)
    echo "  Windows: $WINDOW_COUNT"
else
    echo -e "${RED}✗ tmux session 'ccbot' not found${NC}"
    echo "  Fix: tmux new-session -d -s ccbot"
fi

# Summary
echo ""
echo "=== Summary ==="
if [ "$CCBOT_PROCS" -eq 0 ]; then
    echo -e "${RED}Action required: Start ccbot${NC}"
    echo "  Run: tmux send-keys -t ccbot:__main__ 'ccbot run' Enter"
elif [ "$CCBOT_PROCS" -gt 1 ]; then
    echo -e "${RED}Action required: Kill duplicate ccbot instances${NC}"
    echo "  Multiple instances cause Telegram API conflicts"
elif tail -100 ~/.ccbot/ccbot.log 2>/dev/null | grep -qi "rate limit\|retry after"; then
    echo -e "${RED}Action required: Swap bot token${NC}"
    echo "  See docs/CCBOT_FAILURES_AND_FIXES.md for token swap procedure"
else
    echo -e "${GREEN}All checks passed! ccbot appears healthy.${NC}"
fi

echo ""
echo "For detailed troubleshooting, see: docs/CCBOT_FAILURES_AND_FIXES.md"
