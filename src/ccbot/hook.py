"""Hook subcommand for Claude Code session tracking.

Called by Claude Code's SessionStart hook to maintain a window↔session
mapping in <CCBOT_DIR>/session_map.json. Also provides `--install` to
auto-configure the hook in ~/.claude/settings.json.

This module must NOT import config.py (which requires TELEGRAM_BOT_TOKEN),
since hooks run inside tmux panes where bot env vars are not set.
Config directory resolution uses utils.ccbot_dir() (shared with config.py).

Key functions: hook_main() (CLI entry), _install_hook().
"""

import fcntl
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Validate session_id looks like a UUID
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# Substring marker for detecting ccbot hook in command strings
_HOOK_COMMAND_MARKER = "ccbot hook"

# Expected number of parts when parsing "session_name:@id:window_name"
_TMUX_FORMAT_PARTS = 3


def _is_hook_installed(settings: dict) -> bool:
    """Check if ccbot hook is already installed in the settings.

    Detects 'ccbot hook' anywhere in the command string, covering:
    - Bare: 'ccbot hook'
    - Full path: '/usr/bin/ccbot hook'
    - With shell wrappers: 'ccbot hook 2>/dev/null || true'
    """
    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            if _HOOK_COMMAND_MARKER in cmd:
                return True
    return False


def _install_hook() -> int:
    """Install the ccbot hook into Claude's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.exception("Error reading %s", settings_file)
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    # Check if already installed
    if _is_hook_installed(settings):
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    # Use PATH-relative command for portability across machines
    hook_command = "ccbot hook"
    hook_config = {"type": "command", "command": hook_command, "timeout": 5}
    logger.info("Installing hook command: %s", hook_command)

    # Install the hook into an existing matcher group if one exists,
    # otherwise create a new SessionStart entry
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    session_start = settings["hooks"]["SessionStart"]
    if session_start:
        # Add to the first matcher group's hooks array
        first_entry = session_start[0]
        if isinstance(first_entry, dict):
            first_entry.setdefault("hooks", []).append(hook_config)
        else:
            session_start.append({"hooks": [hook_config]})
    else:
        session_start.append({"hooks": [hook_config]})

    # Write back
    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        logger.exception("Error writing %s", settings_file)
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    logger.info("Hook installed successfully in %s", settings_file)
    print(f"Hook installed successfully in {settings_file}")
    return 0


def _uninstall_hook() -> int:
    """Remove the ccbot hook from Claude's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    if not settings_file.exists():
        print("No settings.json found — nothing to uninstall.")
        return 0

    try:
        settings = json.loads(settings_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {settings_file}: {e}", file=sys.stderr)
        return 1

    if not _is_hook_installed(settings):
        print("Hook not installed — nothing to uninstall.")
        return 0

    # Remove ccbot hook entries from SessionStart
    session_start = settings.get("hooks", {}).get("SessionStart", [])
    new_session_start = []
    for entry in session_start:
        if not isinstance(entry, dict):
            new_session_start.append(entry)
            continue
        inner_hooks = entry.get("hooks", [])
        filtered = [
            h
            for h in inner_hooks
            if not isinstance(h, dict)
            or _HOOK_COMMAND_MARKER not in h.get("command", "")
        ]
        if filtered:
            entry["hooks"] = filtered
            new_session_start.append(entry)

    settings["hooks"]["SessionStart"] = new_session_start

    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    print(f"Hook uninstalled from {settings_file}")
    return 0


def _hook_status() -> int:
    """Show hook installation status.

    Returns 0 if installed, 1 if not.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    if not settings_file.exists():
        print(f"Not installed ({settings_file} does not exist)")
        return 1

    try:
        settings = json.loads(settings_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {settings_file}: {e}", file=sys.stderr)
        return 1

    if _is_hook_installed(settings):
        # Find the command path
        for entry in settings.get("hooks", {}).get("SessionStart", []):
            if not isinstance(entry, dict):
                continue
            for h in entry.get("hooks", []):
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command", "")
                if _HOOK_COMMAND_MARKER in cmd:
                    print(f"Installed: {cmd}")
                    return 0
        print("Installed")
        return 0

    print("Not installed")
    return 1


def _process_hook_stdin() -> None:
    """Process a Claude Code hook event from stdin."""
    logger.debug("Processing hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    transcript_path = payload.get("transcript_path", "")
    event = payload.get("hook_event_name", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    # Validate session_id format
    if not UUID_RE.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    # Validate cwd is an absolute path (if provided)
    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return

    if event != "SessionStart":
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

    # Get tmux session:window key for the pane running this hook.
    # TMUX_PANE is set by tmux for every process inside a pane.
    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.warning("TMUX_PANE not set, cannot determine window")
        return

    result = subprocess.run(
        [
            "tmux",
            "display-message",
            "-t",
            pane_id,
            "-p",
            "#{session_name}:#{window_id}:#{window_name}",
        ],
        capture_output=True,
        text=True,
    )
    raw_output = result.stdout.strip()
    # Expected format: "session_name:@id:window_name"
    parts = raw_output.split(":", 2)
    if len(parts) < _TMUX_FORMAT_PARTS:
        logger.warning(
            "Failed to parse session:window_id:window_name from tmux (pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return
    tmux_session_name, window_id, window_name = parts
    # Key uses window_id for uniqueness
    session_window_key = f"{tmux_session_name}:{window_id}"

    logger.debug(
        "tmux key=%s, window_name=%s, session_id=%s, cwd=%s",
        session_window_key,
        window_name,
        session_id,
        cwd,
    )

    # Read-modify-write with file locking to prevent concurrent hook races
    from .utils import ccbot_dir

    map_file = ccbot_dir() / "session_map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)

    lock_path = map_file.with_suffix(".lock")
    try:
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            logger.debug("Acquired lock on %s", lock_path)
            try:
                session_map: dict[str, dict[str, str]] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text())
                    except json.JSONDecodeError, OSError:
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                session_map[session_window_key] = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                    "transcript_path": transcript_path,
                }

                # Clean up old-format key ("session:window_name") if it exists.
                # Previous versions keyed by window_name instead of window_id.
                old_key = f"{tmux_session_name}:{window_name}"
                if old_key != session_window_key and old_key in session_map:
                    del session_map[old_key]
                    logger.info("Removed old-format session_map key: %s", old_key)

                from .utils import atomic_write_json

                atomic_write_json(map_file, session_map)
                logger.info(
                    "Updated session_map: %s -> session_id=%s, cwd=%s",
                    session_window_key,
                    session_id,
                    cwd,
                )
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError:
        logger.exception("Failed to write session_map")


def hook_main(
    install: bool = False, uninstall: bool = False, status: bool = False
) -> None:
    """Process a Claude Code hook event from stdin, or manage hook installation."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    if install:
        logger.info("Hook install requested")
        sys.exit(_install_hook())

    if uninstall:
        sys.exit(_uninstall_hook())

    if status:
        sys.exit(_hook_status())

    _process_hook_stdin()
