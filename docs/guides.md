# Guides

## Upgrading

```bash
uv tool upgrade ccbot                # uv (recommended)
pipx upgrade ccbot                   # pipx
brew upgrade ccbot                   # Homebrew
```

## CLI Reference

```
ccbot                        # Start the bot
ccbot status                 # Show running state (no token needed)
ccbot doctor                 # Validate setup and diagnose issues
ccbot doctor --fix           # Auto-fix issues (install hook, kill orphans)
ccbot hook --install         # Install Claude Code SessionStart hook
ccbot hook --uninstall       # Remove the hook
ccbot hook --status          # Check if hook is installed
ccbot --version              # Show version
ccbot -v                     # Run with debug logging
```

## Configuration

All settings accept both CLI flags and environment variables. CLI flags take precedence. `TELEGRAM_BOT_TOKEN` is env-only for security (flags are visible in `ps`).

| Variable / Flag                                | Default        | Description                                      |
| ---------------------------------------------- | -------------- | ------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN`                           | _(required)_   | Bot token from @BotFather (env only)             |
| `ALLOWED_USERS` / `--allowed-users`            | _(required)_   | Comma-separated Telegram user IDs                |
| `CCBOT_DIR` / `--config-dir`                   | `~/.ccbot`     | Config and state directory                       |
| `TMUX_SESSION_NAME` / `--tmux-session`         | `ccbot`        | tmux session name                                |
| `CLAUDE_COMMAND` / `--claude-command`          | `claude`       | Command to launch Claude Code                    |
| `CCBOT_GROUP_ID` / `--group-id`                | _(all groups)_ | Restrict to one Telegram group                   |
| `CCBOT_INSTANCE_NAME` / `--instance-name`      | hostname       | Display label for this instance                  |
| `CCBOT_LOG_LEVEL` / `--log-level`              | `INFO`         | Logging level (DEBUG, INFO, WARNING, ERROR)      |
| `MONITOR_POLL_INTERVAL` / `--monitor-interval` | `2.0`          | Seconds between transcript polls                 |
| `AUTOCLOSE_DONE_MINUTES` / `--autoclose-done`  | `30`           | Auto-close done topics after N minutes (0=off)   |
| `AUTOCLOSE_DEAD_MINUTES` / `--autoclose-dead`  | `10`           | Auto-close dead sessions after N minutes (0=off) |

## Auto-Close Behavior

CCBot automatically closes Telegram topics when sessions end, reducing clutter:

- **Done topics** (`--autoclose-done`, default: 30 min) — When Claude finishes a task and the session completes normally, the topic auto-closes after 30 minutes.
- **Dead sessions** (`--autoclose-dead`, default: 10 min) — When a Claude process crashes or the tmux window is killed externally, the topic auto-closes after 10 minutes.

Set to `0` to disable:

```bash
ccbot --autoclose-done 0 --autoclose-dead 0
```

## Multi-Instance Setup

Run multiple ccbot instances on the same machine, each owning a different Telegram group. All instances can share a single bot token.

**Example: work + personal instances**

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

Each instance uses a separate tmux session, config directory, and state. When `CCBOT_GROUP_ID` is set, an instance silently ignores updates from other groups.

Without `CCBOT_GROUP_ID`, a single instance processes all groups (the default).

> To find your group's chat ID, add [@RawDataBot](https://t.me/RawDataBot) to the group — it replies with the chat ID (a negative number like `-1001234567890`).

## Creating Sessions from the Terminal

Besides creating sessions through Telegram topics, you can create tmux windows directly:

```bash
# Attach to the ccbot tmux session
tmux attach -t ccbot

# Create a new window for your project
tmux new-window -n myproject -c ~/Code/myproject

# Start Claude Code
claude
```

The window must be in the ccbot tmux session (configurable via `TMUX_SESSION_NAME`). When Claude starts, the SessionStart hook registers it automatically and the bot creates a matching Telegram topic.

This works even on a fresh instance with no existing topic bindings (cold-start).

## Session Recovery

When a Claude Code session exits or crashes, the bot detects the dead window and offers recovery options via inline buttons:

- **Fresh** — Kill the old window, create a new one in the same directory
- **Continue** — Start a new Claude session using `--continue` to resume the last conversation
- **Resume** — Browse and select a past session to resume from

## Data Storage

All state files live in `$CCBOT_DIR` (`~/.ccbot/` by default):

| File                 | Description                                                 |
| -------------------- | ----------------------------------------------------------- |
| `state.json`         | Thread bindings, window states, display names, read offsets |
| `session_map.json`   | Hook-generated window → session mappings                    |
| `monitor_state.json` | Byte offsets per session (prevents duplicate notifications) |

Claude Code session transcripts are read from `~/.claude/projects/` (read-only). The bot never writes to Claude's data directory.

## Running as a Service

For persistent operation, run ccbot as a systemd service or under a process manager:

```bash
# systemd user service (~/.config/systemd/user/ccbot.service)
[Unit]
Description=CCBot - Telegram bridge for Claude Code
After=network.target

[Service]
ExecStart=%h/.local/bin/ccbot
Restart=on-failure
RestartSec=5
Environment=CCBOT_DIR=%h/.ccbot

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable ccbot
systemctl --user start ccbot
```

On macOS, you can use a launchd plist or simply run in a detached tmux session:

```bash
tmux new-session -d -s ccbot-daemon 'ccbot'
```
