"""Helpers for Claude Code's own config file (~/.claude.json).

Pre-trusts a working directory before launching `claude` in it so the
interactive "Quick safety check … Yes, I trust this folder" dialog never
appears. Since ~2.1.260 that dialog defaults to "No, exit", which left
bot-created sessions stuck at the prompt with the SessionStart hook never
firing.

Key functions:
  - claude_config_file(): path of ~/.claude.json (honours CLAUDE_CONFIG_DIR)
  - trust_key_for(work_dir): the projects[] key Claude uses (git root or dir)
  - ensure_trusted_directory(work_dir): set hasTrustDialogAccepted=true
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .utils import atomic_write_json

logger = logging.getLogger(__name__)


def claude_config_file() -> Path:
    """Location of Claude Code's user config JSON."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if config_dir:
        return Path(config_dir).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def trust_key_for(work_dir: str | Path) -> str:
    """Return the ``projects`` key Claude Code trusts ``work_dir`` under.

    Trust is keyed on the git repository root (main checkout root for
    worktrees); outside a repo, on the directory itself.
    """
    path = Path(work_dir).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return str(Path(result.stdout.strip()).resolve())
    except (OSError, subprocess.SubprocessError):
        pass
    return str(path)


def ensure_trusted_directory(work_dir: str | Path) -> bool:
    """Mark ``work_dir`` as trusted in ~/.claude.json.

    Returns True when the directory is trusted after the call (already
    trusted or newly written), False when the config could not be updated
    (missing/malformed file is left untouched — the pane-level dialog
    handling in TmuxManager remains as the fallback).
    """
    key = trust_key_for(work_dir)
    cfg_path = claude_config_file()
    if not cfg_path.exists():
        logger.info("No %s yet; skipping trust pre-accept for %s", cfg_path, key)
        return False
    try:
        data: Any = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Cannot read %s (%s); not touching it", cfg_path, e)
        return False
    if not isinstance(data, dict):
        logger.warning("%s is not a JSON object; not touching it", cfg_path)
        return False

    projects = data.get("projects")
    if projects is None:
        projects = data["projects"] = {}
    if not isinstance(projects, dict):
        logger.warning("%s: 'projects' is not an object; not touching it", cfg_path)
        return False

    entry = projects.get(key)
    if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
        return True
    if not isinstance(entry, dict):
        entry = projects[key] = {}
    entry["hasTrustDialogAccepted"] = True

    try:
        mode = cfg_path.stat().st_mode & 0o777
        atomic_write_json(cfg_path, data)
        os.chmod(cfg_path, mode)
    except OSError as e:
        logger.warning("Failed to write %s: %s", cfg_path, e)
        return False
    logger.info("Pre-trusted %s in %s", key, cfg_path)
    return True
