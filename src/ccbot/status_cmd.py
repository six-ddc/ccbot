"""CLI `ccbot status` — show running state without bot token.

Reads state files and tmux directly to display:
  - ccbot version
  - Tmux session info (name, window count)
  - Per-window status: bound/unbound, alive/dead

No Config import needed — uses utils.ccbot_dir() and subprocess for tmux.
"""

import json
import subprocess
import sys
from pathlib import Path

from .utils import ccbot_dir, tmux_session_name

_TMUX_FORMAT_PARTS = 2


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning empty dict on any error."""
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError, OSError:
        return {}


def _list_tmux_windows(session_name: str) -> list[dict[str, str]]:
    """List tmux windows via subprocess. Returns list of {id, name}."""
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-windows",
                "-t",
                session_name,
                "-F",
                "#{window_id}\t#{window_name}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == _TMUX_FORMAT_PARTS:
                windows.append({"id": parts[0], "name": parts[1]})
        return windows
    except OSError, subprocess.TimeoutExpired:
        return []


def _capability_summary() -> tuple[str, str]:
    """Return (provider_name, comma-separated capability flags)."""
    from .providers import resolve_capabilities

    caps = resolve_capabilities()
    flags = [
        label
        for flag, label in (
            (caps.supports_hook, "hook"),
            (caps.supports_resume, "resume"),
            (caps.supports_continue, "continue"),
        )
        if flag
    ]
    return caps.name, ", ".join(flags) or "none"


def status_main() -> None:
    """Entry point for `ccbot status`."""
    from . import __version__

    provider_name, cap_flags = _capability_summary()
    config_dir = ccbot_dir()
    session_name = tmux_session_name()

    # Read state files
    state = _read_json(config_dir / "state.json")
    session_map = _read_json(config_dir / "session_map.json")

    # Get live tmux windows
    live_windows = _list_tmux_windows(session_name)

    # Build binding index: window_id -> (thread_id, user_id)
    thread_bindings = state.get("thread_bindings", {})
    display_names = state.get("window_display_names", {})
    bound_windows: dict[str, tuple[int, int]] = {}
    for user_id_str, bindings in thread_bindings.items():
        for thread_id_str, window_id in bindings.items():
            bound_windows[window_id] = (int(thread_id_str), int(user_id_str))

    # Count monitored sessions
    prefix = f"{session_name}:"
    monitored = sum(1 for k in session_map if k.startswith(prefix))

    # Output
    print(f"ccbot {__version__}")
    print(f"Provider: {provider_name} ({cap_flags})")
    print(f"Tmux session: {session_name} ({len(live_windows)} windows)")
    print(f"Monitored sessions: {monitored}")

    if not live_windows and not bound_windows:
        return

    print()

    # Show live windows first
    shown_ids: set[str] = set()
    for w in live_windows:
        wid = w["id"]
        name = display_names.get(wid, w["name"])
        shown_ids.add(wid)

        if wid in bound_windows:
            thread_id, user_id = bound_windows[wid]
            print(
                f"  {wid:<5} {name:<16} -> topic {thread_id} (user {user_id})   alive"
            )
        else:
            print(f"  {wid:<5} {name:<16}                              (unbound)")

    # Show dead bindings (bound but window gone)
    for wid, (thread_id, user_id) in bound_windows.items():
        if wid not in shown_ids:
            name = display_names.get(wid, wid)
            print(f"  {wid:<5} {name:<16} -> topic {thread_id} (user {user_id})   dead")

    sys.exit(0)
