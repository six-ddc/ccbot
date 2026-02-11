# Guides

## Multi-Instance Setup

Multiple ccbot instances can share a single Telegram bot token, each owning a different Telegram group. When `CCBOT_GROUP_ID` is set, an instance silently ignores updates from other groups.

**Example: two instances on the same machine**

Instance 1 (`~/.ccbot-work/.env`):

```ini
TELEGRAM_BOT_TOKEN=same_token_for_both
ALLOWED_USERS=123456789
CCBOT_GROUP_ID=-1001111111111
CCBOT_INSTANCE_NAME=work
CCBOT_DIR=~/.ccbot-work
TMUX_SESSION_NAME=ccbot-work
```

Instance 2 (`~/.ccbot-personal/.env`):

```ini
TELEGRAM_BOT_TOKEN=same_token_for_both
ALLOWED_USERS=123456789
CCBOT_GROUP_ID=-1002222222222
CCBOT_INSTANCE_NAME=personal
CCBOT_DIR=~/.ccbot-personal
TMUX_SESSION_NAME=ccbot-personal
```

Run both:

```bash
CCBOT_DIR=~/.ccbot-work ccbot &
CCBOT_DIR=~/.ccbot-personal ccbot &
```

Without `CCBOT_GROUP_ID`, a single instance processes all groups (the default).

To find your group's chat ID, add [@userinfobot](https://t.me/userinfobot) or [@RawDataBot](https://t.me/RawDataBot) to the group — it will reply with the chat ID (a negative number like `-1001234567890`).

## Creating Sessions Manually

Besides creating sessions through Telegram, you can create tmux windows directly:

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
claude
```

The window must be in the `ccbot` tmux session (configurable via `TMUX_SESSION_NAME`). The hook will register it automatically when Claude starts.

## Data Storage

All state files live in `$CCBOT_DIR` (`~/.ccbot/` by default):

| File                 | Description                                                 |
| -------------------- | ----------------------------------------------------------- |
| `state.json`         | Thread bindings, window states, display names, read offsets |
| `session_map.json`   | Hook-generated window → session mappings                    |
| `monitor_state.json` | Byte offsets per session (prevents duplicate notifications) |

Claude Code session transcripts are read from `~/.claude/projects/` (read-only).
