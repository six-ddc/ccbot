"""CLI `ccbot doctor [--fix]` — validate ccbot setup.

Checks environment, dependencies, and configuration without requiring
a bot token. With --fix, auto-repairs what it can (install hook, kill orphans).

Provider-aware: reads CCBOT_PROVIDER env to determine which checks apply
(e.g. hook checks are skipped for providers without hook support).
No Config import needed — uses utils.ccbot_dir() and subprocess.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from collections.abc import Callable

from .providers import resolve_capabilities
from .utils import ccbot_dir, tmux_session_name

_PASS = "pass"
_FAIL = "fail"
_WARN = "warn"

_SYMBOLS = {_PASS: "\u2713", _FAIL: "\u2717", _WARN: "\u26a0"}

_TMUX_FORMAT_PARTS = 2
_MAIN_WINDOW_NAME = "__main__"


def _print_check(status: str, message: str) -> None:
    """Print a single check result."""
    sym = _SYMBOLS.get(status, "?")
    print(f"  {sym} {message}")


def _check_tmux() -> tuple[str, str]:
    """Check tmux binary and version."""
    path = shutil.which("tmux")
    if not path:
        return _FAIL, "tmux not found in PATH"
    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip()
        return _PASS, f"{version} found"
    except OSError, subprocess.TimeoutExpired:
        return _PASS, "tmux found (version unknown)"


def _check_provider_command(launch_command: str) -> tuple[str, str]:
    """Check provider CLI command availability."""
    cmd = os.environ.get("CCBOT_PROVIDER_COMMAND", launch_command)
    path = shutil.which(cmd)
    if path:
        return _PASS, f"{cmd} found at {path}"
    return _FAIL, f"'{cmd}' not found in PATH"


def _check_tmux_session() -> tuple[str, str]:
    """Check if tmux session exists."""
    session_name = tmux_session_name()
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return _PASS, f'tmux session "{session_name}" exists'
        return _FAIL, f'tmux session "{session_name}" not found'
    except OSError, subprocess.TimeoutExpired:
        return _FAIL, "cannot connect to tmux server"


def _check_hook() -> tuple[str, str, bool]:
    """Check hook installation. Returns (status, message, is_installed)."""
    from .hook import _CLAUDE_SETTINGS_FILE, _is_hook_installed

    if not _CLAUDE_SETTINGS_FILE.exists():
        return _FAIL, "hook not installed (~/.claude/settings.json missing)", False
    try:
        settings = json.loads(_CLAUDE_SETTINGS_FILE.read_text())
    except json.JSONDecodeError, OSError:
        return _FAIL, "hook not installed (settings.json unreadable)", False

    if _is_hook_installed(settings):
        return _PASS, "hook installed in ~/.claude/settings.json", True
    return _FAIL, "hook not installed in ~/.claude/settings.json", False


def _check_config_dir() -> tuple[str, str]:
    """Check config directory exists."""
    config_dir = ccbot_dir()
    if config_dir.is_dir():
        return _PASS, f"config dir {config_dir} exists"
    return _FAIL, f"config dir {config_dir} not found"


def _check_bot_token() -> tuple[str, str]:
    """Check bot token is set (without printing it)."""
    from dotenv import load_dotenv

    config_dir = ccbot_dir()
    local_env = Path(".env")
    global_env = config_dir / ".env"
    if local_env.is_file():
        load_dotenv(local_env)
    if global_env.is_file():
        load_dotenv(global_env)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        return _PASS, "TELEGRAM_BOT_TOKEN set"
    return _FAIL, "TELEGRAM_BOT_TOKEN not set"


def _check_allowed_users() -> tuple[str, str]:
    """Check allowed users configured."""
    users_str = os.environ.get("ALLOWED_USERS", "")
    if not users_str:
        return _FAIL, "ALLOWED_USERS not set"
    try:
        users = [int(u.strip()) for u in users_str.split(",") if u.strip()]
        return _PASS, f"ALLOWED_USERS: {len(users)} user(s)"
    except ValueError:
        return _FAIL, "ALLOWED_USERS contains non-numeric values"


def _list_live_windows(session_name: str) -> dict[str, str]:
    """List live tmux windows, excluding __main__. Returns {window_id: window_name}."""
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
            return {}
    except OSError, subprocess.TimeoutExpired:
        return {}

    windows: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == _TMUX_FORMAT_PARTS and parts[1] != _MAIN_WINDOW_NAME:
            windows[parts[0]] = parts[1]
    return windows


def _get_known_window_ids(config_dir: Path, session_name: str) -> set[str]:
    """Get window IDs known from state.json bindings and session_map.json."""
    known: set[str] = set()

    state_file = config_dir / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            for bindings in state.get("thread_bindings", {}).values():
                known.update(bindings.values())
        except json.JSONDecodeError, OSError:
            pass

    session_map_file = config_dir / "session_map.json"
    prefix = f"{session_name}:"
    if session_map_file.exists():
        try:
            session_map = json.loads(session_map_file.read_text())
            for key in session_map:
                if key.startswith(prefix):
                    known.add(key[len(prefix) :])
        except json.JSONDecodeError, OSError:
            pass

    return known


def _find_orphaned_windows() -> list[tuple[str, str]]:
    """Find tmux windows not bound to any topic and not in session_map."""
    session_name = tmux_session_name()
    live_windows = _list_live_windows(session_name)
    if not live_windows:
        return []

    known_ids = _get_known_window_ids(ccbot_dir(), session_name)
    return [(wid, wname) for wid, wname in live_windows.items() if wid not in known_ids]


def _run_check(check_fn: Callable[[], tuple[str, str]]) -> tuple[str, str, bool]:
    """Run a check function and return (status, message, is_failure)."""
    result = check_fn()
    status, msg = result[0], result[1]
    _print_check(status, msg)
    return status, msg, status == _FAIL


def _fix_hook(hook_installed: bool, fix: bool) -> None:
    """Attempt to fix missing hook if --fix is set."""
    if not fix or hook_installed:
        return
    from .hook import _install_hook

    result = _install_hook()
    if result == 0:
        _print_check(_PASS, "hook installed (fixed)")
    else:
        _print_check(_FAIL, "failed to install hook")


def _fix_orphans(orphans: list[tuple[str, str]], fix: bool) -> None:
    """Kill orphaned windows if --fix is set."""
    if not fix:
        return
    session_name = tmux_session_name()
    for wid, wname in orphans:
        try:
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{session_name}:{wid}"],
                capture_output=True,
                timeout=5,
            )
            _print_check(_PASS, f"killed orphaned window {wid} ({wname})")
        except OSError, subprocess.TimeoutExpired:
            _print_check(_FAIL, f"failed to kill window {wid}")


def doctor_main(fix: bool = False) -> None:
    """Entry point for `ccbot doctor [--fix]`."""
    caps = resolve_capabilities()
    has_failures = False

    print(f"Provider: {caps.name}")

    # Core checks
    _, _, failed = _run_check(_check_tmux)
    has_failures = has_failures or failed

    _, _, failed = _run_check(lambda: _check_provider_command(caps.launch_command))
    has_failures = has_failures or failed

    _, _, failed = _run_check(_check_tmux_session)
    has_failures = has_failures or failed

    # Hook check — only relevant for providers with hook support
    if caps.supports_hook:
        hook_status, hook_msg, hook_installed = _check_hook()
        _print_check(hook_status, hook_msg)
        if hook_status == _FAIL:
            has_failures = True
            _fix_hook(hook_installed, fix)
    else:
        _print_check(_PASS, f"hook check skipped ({caps.name} has no hook support)")

    for check_fn in (_check_config_dir, _check_bot_token, _check_allowed_users):
        _, _, failed = _run_check(check_fn)
        has_failures = has_failures or failed

    # Orphaned windows
    orphans = _find_orphaned_windows()
    if orphans:
        names = ", ".join(f"{wid} ({wname})" for wid, wname in orphans)
        _print_check(_WARN, f"{len(orphans)} orphaned window(s): {names}")
        _fix_orphans(orphans, fix)
    else:
        _print_check(_PASS, "no orphaned windows")

    sys.exit(1 if has_failures else 0)
