"""Hook subcommand for Claude Code or Codex session tracking.

Supports:
  - `ccbot hook --install` to install hooks for Claude Code or Codex
  - `ccbot hook --install-codex` as a backwards-compatible Codex alias

At runtime, the hook reads a `SessionStart` payload from stdin and updates
`<CCBOT_DIR>/session_map.json` with the current tmux window metadata.
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .runtimes import RUNTIME_CLAUDE, RUNTIME_CODEX
from .session_register import register_session

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
_HOOK_COMMAND_SUFFIX = "ccbot hook"


def _codex_hooks_file() -> Path:
    codex_home = os.getenv("CODEX_HOME", "").strip()
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "hooks.json"


def _find_ccbot_path() -> str:
    """Find the full path to the ccbot executable."""
    ccbot_path = shutil.which("ccbot")
    if ccbot_path:
        return ccbot_path

    python_dir = Path(sys.executable).parent
    ccbot_in_venv = python_dir / "ccbot"
    if ccbot_in_venv.exists():
        return str(ccbot_in_venv)

    return "ccbot"


def _has_command_hook(entries: list[object]) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command", "")
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Error reading %s: %s", path, e)
        raise
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_file(path: Path, data: dict) -> int:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("Error writing %s: %s", path, e)
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return 1
    return 0


def _install_claude_hook() -> int:
    """Install the hook into Claude's settings.json."""
    settings_file = _CLAUDE_SETTINGS_FILE
    try:
        settings = _read_json_file(settings_file)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(f"Error reading {settings_file}: {e}", file=sys.stderr)
        return 1

    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        print(f"Error: {settings_file} hooks.SessionStart must be a list", file=sys.stderr)
        return 1

    if _has_command_hook(session_start):
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    ccbot_path = _find_ccbot_path()
    hook_config = {
        "type": "command",
        "command": f"{ccbot_path} hook",
        "timeout": 5,
    }
    session_start.append({"hooks": [hook_config]})
    result = _write_json_file(settings_file, settings)
    if result == 0:
        logger.info("Hook installed successfully in %s", settings_file)
        print(f"Hook installed successfully in {settings_file}")
    return result


def _install_codex_hook() -> int:
    """Install the hook into Codex's hooks.json."""
    hooks_file = _codex_hooks_file()
    try:
        settings = _read_json_file(hooks_file)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(f"Error reading {hooks_file}: {e}", file=sys.stderr)
        return 1

    migrated = False

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print(f"Error: {hooks_file} hooks must be an object", file=sys.stderr)
        return 1

    legacy_session_start = settings.pop("SessionStart", None)
    if legacy_session_start is not None:
        migrated = True
        if "SessionStart" not in hooks:
            hooks["SessionStart"] = legacy_session_start

    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        print(f"Error: {hooks_file} hooks.SessionStart must be a list", file=sys.stderr)
        return 1

    if _has_command_hook(session_start):
        if migrated:
            result = _write_json_file(hooks_file, settings)
            if result != 0:
                return result
        logger.info("Hook already installed in %s", hooks_file)
        print(f"Hook already installed in {hooks_file}")
        return 0

    ccbot_path = _find_ccbot_path()
    hook_config = {
        "type": "command",
        "command": f"{ccbot_path} hook",
        "timeout": 5,
    }
    session_start.append({"hooks": [hook_config]})
    result = _write_json_file(hooks_file, settings)
    if result == 0:
        logger.info("Hook installed successfully in %s", hooks_file)
        print(f"Hook installed successfully in {hooks_file}")
    return result


def _resolve_install_targets(explicit_runtime: str | None) -> list[str]:
    """Resolve which Claude Code / Codex hook targets to install."""
    if explicit_runtime == "all":
        return [RUNTIME_CLAUDE, RUNTIME_CODEX]
    if explicit_runtime in {RUNTIME_CLAUDE, RUNTIME_CODEX}:
        return [explicit_runtime]

    env_runtime = os.getenv("CCBOT_RUNTIME", "").strip()
    if env_runtime in {RUNTIME_CLAUDE, RUNTIME_CODEX}:
        return [env_runtime]

    return [RUNTIME_CLAUDE, RUNTIME_CODEX]


def _install_selected_hooks(explicit_runtime: str | None) -> int:
    """Install hooks for the selected Claude Code / Codex targets."""
    installers = {
        RUNTIME_CLAUDE: _install_claude_hook,
        RUNTIME_CODEX: _install_codex_hook,
    }
    status = 0
    for target in _resolve_install_targets(explicit_runtime):
        result = installers[target]()
        if result != 0:
            status = result
    return status


def hook_main() -> None:
    """Process a Claude Code / Codex hook event from stdin, or install hooks."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="ccbot hook",
        description="Claude Code / Codex session tracking hook",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Install hooks for Claude Code or Codex. "
            "Uses --run/--runtime, then CCBOT_RUNTIME, otherwise installs both."
        ),
    )
    parser.add_argument(
        "--run",
        "--runtime",
        dest="runtime",
        choices=[RUNTIME_CLAUDE, RUNTIME_CODEX, "all"],
        help="Hook target for --install: claude, codex, or all",
    )
    parser.add_argument(
        "--install-codex",
        action="store_true",
        help="Backwards-compatible alias for --install --run codex",
    )
    args, _ = parser.parse_known_args(sys.argv[2:])

    if args.install:
        logger.info("Hook install requested for %s", args.runtime or "auto")
        sys.exit(_install_selected_hooks(args.runtime))
    if args.install_codex:
        logger.info("Codex hook install requested")
        sys.exit(_install_codex_hook())

    logger.debug("Processing hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id") or payload.get("id", "")
    cwd = payload.get("cwd", "")
    event = payload.get("hook_event_name", "")
    transcript_path = payload.get("transcript_path", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    if not _UUID_RE.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return
    if transcript_path and not os.path.isabs(transcript_path):
        logger.warning("transcript_path is not absolute: %s", transcript_path)
        return

    if event != "SessionStart":
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

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
    parts = raw_output.split(":", 2)
    if len(parts) < 3:
        logger.warning(
            "Failed to parse session:window_id:window_name from tmux (pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return

    tmux_session_name, window_id, window_name = parts
    runtime = os.getenv("CCBOT_RUNTIME", "").strip()
    if runtime not in {RUNTIME_CLAUDE, RUNTIME_CODEX}:
        runtime = RUNTIME_CODEX if transcript_path else RUNTIME_CLAUDE

    logger.debug(
        "tmux key=%s:%s, window_name=%s, session_id=%s, cwd=%s, runtime=%s, transcript=%s",
        tmux_session_name,
        window_id,
        window_name,
        session_id,
        cwd,
        runtime,
        transcript_path,
    )

    ok = register_session(
        window_id=window_id,
        session_id=session_id,
        cwd=cwd,
        window_name=window_name,
        runtime=runtime,
        transcript_path=transcript_path,
        tmux_session_name=tmux_session_name,
    )
    if not ok:
        logger.error(
            "Failed to register runtime session for %s:%s (%s)",
            tmux_session_name,
            window_id,
            session_id,
        )
