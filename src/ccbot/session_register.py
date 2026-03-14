"""Session registration helpers for tmux-backed Claude Code / Codex sessions.

Provides a CLI-friendly way for hook subprocesses to register tmux window
metadata in ``session_map.json`` without importing the main config module.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sys

from .runtimes import RUNTIME_CLAUDE, SUPPORTED_RUNTIMES
from .utils import atomic_write_json, ccbot_dir

logger = logging.getLogger(__name__)

_WINDOW_ID_RE = re.compile(r"^@\d+$")


def build_session_map_key(window_id: str, tmux_session_name: str | None = None) -> str:
    """Build the persisted tmux session_map key for a window."""
    session_name = tmux_session_name or os.getenv("TMUX_SESSION_NAME", "ccbot")
    return f"{session_name}:{window_id}"


def register_session(
    *,
    window_id: str,
    session_id: str,
    cwd: str,
    window_name: str = "",
    runtime: str = RUNTIME_CLAUDE,
    transcript_path: str = "",
    tmux_session_name: str | None = None,
) -> bool:
    """Register or update a tmux window -> Claude Code / Codex session mapping."""
    if not _WINDOW_ID_RE.match(window_id):
        logger.warning("Invalid tmux window_id: %s", window_id)
        return False
    if not session_id.strip():
        logger.warning("Empty session_id, ignoring session registration")
        return False
    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return False
    if transcript_path and not os.path.isabs(transcript_path):
        logger.warning("transcript_path is not absolute: %s", transcript_path)
        return False
    if runtime not in SUPPORTED_RUNTIMES:
        logger.warning("Unsupported runtime: %s", runtime)
        return False

    map_file = ccbot_dir() / "session_map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = map_file.with_suffix(".lock")
    map_key = build_session_map_key(window_id, tmux_session_name)

    try:
        with open(lock_path, "w", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                session_map: dict[str, dict[str, str]] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                session_entry = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                    "runtime": runtime,
                }
                if transcript_path:
                    session_entry["transcript_path"] = transcript_path

                session_map[map_key] = session_entry

                old_key = build_session_map_key(window_name, tmux_session_name)
                if old_key != map_key and old_key in session_map:
                    del session_map[old_key]
                    logger.info("Removed old-format session_map key: %s", old_key)

                atomic_write_json(map_file, session_map)
                logger.info(
                    "Updated session_map: %s -> session_id=%s runtime=%s cwd=%s",
                    map_key,
                    session_id,
                    runtime,
                    cwd,
                )
                return True
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)
        return False


def session_register_main() -> None:
    """CLI entry point for registering a Claude Code / Codex session."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="ccbot session-register",
        description="Register a tmux window -> Claude Code / Codex session mapping",
    )
    parser.add_argument("--window-id", required=True, help="tmux window ID, e.g. @12")
    parser.add_argument("--session-id", required=True, help="Runtime session ID")
    parser.add_argument("--cwd", required=True, help="Absolute working directory")
    parser.add_argument("--window-name", default="", help="Display name for the window")
    parser.add_argument(
        "--runtime",
        default=RUNTIME_CLAUDE,
        help=f"Runtime name / tool family ({', '.join(sorted(SUPPORTED_RUNTIMES))})",
    )
    parser.add_argument(
        "--transcript-path",
        default="",
        help="Absolute path to the session transcript file",
    )
    parser.add_argument(
        "--tmux-session-name",
        default=os.getenv("TMUX_SESSION_NAME", "ccbot"),
        help="tmux session name used for the session_map key",
    )
    args = parser.parse_args(sys.argv[2:])

    ok = register_session(
        window_id=args.window_id,
        session_id=args.session_id,
        cwd=args.cwd,
        window_name=args.window_name,
        runtime=args.runtime,
        transcript_path=args.transcript_path,
        tmux_session_name=args.tmux_session_name,
    )
    sys.exit(0 if ok else 1)
