#!/usr/bin/env bash
set -euo pipefail

# Restart ccbot via systemd user service.
# Claude Code sessions live in tmux windows and are unaffected by this restart.
# The bot reconnects to existing sessions via state.json and session_map.json on startup.

echo "Restarting ccbot service..."
systemctl --user restart ccbot

sleep 2

if systemctl --user is-active --quiet ccbot; then
    echo "ccbot restarted successfully."
    echo "----------------------------------------"
    journalctl --user -u ccbot --no-pager -n 20
    echo "----------------------------------------"
else
    echo "Error: ccbot failed to start."
    echo "----------------------------------------"
    journalctl --user -u ccbot --no-pager -n 30
    echo "----------------------------------------"
    exit 1
fi
