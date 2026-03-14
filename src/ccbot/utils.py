"""Shared utility functions used across multiple CCBot modules.

Provides:
  - ccbot_dir(): resolve config directory from CCBOT_DIR env var.
  - atomic_write_json(): crash-safe JSON file writes via temp+rename.
  - read_cwd_from_jsonl(): extract the cwd field from the first JSONL entry.
  - write_shutdown_marker() / read_and_clear_shutdown_marker(): graceful shutdown tracking.
"""

import datetime
import json
import os
import tempfile
from pathlib import Path
from typing import Any

CCBOT_DIR_ENV = "CCBOT_DIR"


def ccbot_dir() -> Path:
    """Resolve config directory from CCBOT_DIR env var or default ~/.ccbot."""
    raw = os.environ.get(CCBOT_DIR_ENV, "")
    return Path(raw) if raw else Path.home() / ".ccbot"


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON data to a file atomically.

    Writes to a temporary file in the same directory, then renames it
    to the target path. This prevents data corruption if the process
    is interrupted mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=indent)

    # Write to temp file in same directory (same filesystem for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=f".{path.name}."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_cwd_from_jsonl(file_path: str | Path) -> str:
    """Read the cwd field from the first JSONL entry that has one.

    Shared by session.py and session_monitor.py.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    cwd = data.get("cwd")
                    if cwd:
                        return cwd
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return ""


_SHUTDOWN_MARKER = ".shutdown_clean"


def write_shutdown_marker() -> None:
    """Write a clean shutdown marker file to config directory.

    Called during graceful shutdown to indicate the bot stopped cleanly.
    On next startup, the bot reads and clears the marker to notify users
    that it has restarted.
    """
    marker = ccbot_dir() / _SHUTDOWN_MARKER
    marker.write_text(
        datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8"
    )


def read_and_clear_shutdown_marker() -> str | None:
    """Read and remove the clean shutdown marker if it exists.

    Returns the timestamp string if a clean shutdown occurred,
    None if the previous shutdown was unclean (crash/SIGKILL).
    """
    marker = ccbot_dir() / _SHUTDOWN_MARKER
    if not marker.exists():
        return None
    try:
        timestamp = marker.read_text(encoding="utf-8").strip()
        marker.unlink()
        return timestamp
    except OSError:
        return None
