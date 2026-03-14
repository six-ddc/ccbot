"""Codex session mapper: tmux windows -> Codex rollout sessions.

Codex does not expose Claude-style SessionStart hooks, so this module
discovers sessions from ~/.codex/sessions/**/rollout-*.jsonl and writes
window mappings into session_map.json in the existing ccbot format.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import config
from .tmux_manager import tmux_manager
from .utils import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class CodexSessionMeta:
    """Metadata extracted from a Codex rollout file."""

    session_id: str
    cwd: str
    file_path: Path
    started_ts: float
    file_mtime: float


def _norm_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, ValueError):
        return path


def _parse_iso_ts(ts: str) -> float:
    if not ts:
        return 0.0
    # Codex timestamps are ISO8601 with trailing Z.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


class CodexSessionMapper:
    """Maps live tmux windows to Codex session IDs."""

    def __init__(
        self,
        sessions_root: Path | None = None,
        session_map_file: Path | None = None,
    ) -> None:
        self.sessions_root = (
            sessions_root if sessions_root is not None else config.codex_sessions_path
        )
        self.session_map_file = (
            session_map_file
            if session_map_file is not None
            else config.session_map_file
        )
        # file -> (mtime, size, parsed_meta_or_none)
        self._meta_cache: dict[str, tuple[float, int, CodexSessionMeta | None]] = {}

    def _read_rollout_meta(
        self, file_path: Path, file_mtime: float
    ) -> CodexSessionMeta | None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                first = f.readline().strip()
        except OSError:
            return None

        if not first:
            return None
        try:
            data = json.loads(first)
        except json.JSONDecodeError:
            return None
        if data.get("type") != "session_meta":
            return None
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            return None

        session_id = payload.get("id", "")
        cwd = payload.get("cwd", "")
        started_at = payload.get("timestamp", "")
        if not session_id or not cwd:
            return None

        return CodexSessionMeta(
            session_id=session_id,
            cwd=_norm_path(cwd),
            file_path=file_path,
            started_ts=_parse_iso_ts(started_at),
            file_mtime=file_mtime,
        )

    def _scan_sessions(self) -> list[CodexSessionMeta]:
        if not self.sessions_root.exists():
            return []

        metas: list[CodexSessionMeta] = []
        seen_files: set[str] = set()
        for file_path in self.sessions_root.rglob("rollout-*.jsonl"):
            key = str(file_path)
            seen_files.add(key)
            try:
                st = file_path.stat()
            except OSError:
                continue
            cached = self._meta_cache.get(key)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                meta = cached[2]
            else:
                meta = self._read_rollout_meta(file_path, st.st_mtime)
                self._meta_cache[key] = (st.st_mtime, st.st_size, meta)
            if meta:
                # Refresh mtime from current stat for cache-hit case
                meta.file_mtime = st.st_mtime
                metas.append(meta)

        # Drop deleted files from cache
        deleted = [k for k in self._meta_cache if k not in seen_files]
        for key in deleted:
            del self._meta_cache[key]

        return metas

    async def sync_session_map(self) -> bool:
        """Sync codex window->session mappings into session_map.json."""
        windows = await tmux_manager.list_windows()
        metas = await asyncio.to_thread(self._scan_sessions)

        if self.session_map_file.exists():
            try:
                session_map = json.loads(self.session_map_file.read_text())
            except (json.JSONDecodeError, OSError):
                session_map = {}
        else:
            session_map = {}

        if not isinstance(session_map, dict):
            session_map = {}

        prefix = f"{config.tmux_session_name}:"
        by_cwd: dict[str, list[CodexSessionMeta]] = {}
        for meta in metas:
            by_cwd.setdefault(meta.cwd, []).append(meta)
        for cwd in by_cwd:
            by_cwd[cwd].sort(
                key=lambda m: (m.file_mtime, m.started_ts),
                reverse=True,
            )
        all_metas = sorted(
            metas,
            key=lambda m: (m.file_mtime, m.started_ts),
            reverse=True,
        )
        preferred_sid = (config.codex_resume_session_id or "").strip()
        preferred_meta: CodexSessionMeta | None = None
        if preferred_sid:
            for meta in all_metas:
                if meta.session_id == preferred_sid:
                    preferred_meta = meta
                    break

        live_wids = {w.window_id for w in windows}
        assigned_session_ids: set[str] = set()
        next_entries: dict[str, dict[str, str]] = {}

        for w in windows:
            key = f"{prefix}{w.window_id}"
            existing = session_map.get(key, {})
            if not isinstance(existing, dict):
                existing = {}
            existing_provider = existing.get("provider", "claude")

            # Map windows that appear active, or were mapped as codex before.
            # Codex often appears as "node" in tmux pane_current_command.
            pane_cmd = (w.pane_current_command or "").lower()
            if existing_provider != "codex" and pane_cmd in (
                "",
                "bash",
                "sh",
                "zsh",
                "fish",
            ):
                continue

            norm_cwd = _norm_path(w.cwd)
            candidates = by_cwd.get(norm_cwd, [])

            chosen: CodexSessionMeta | None = None
            existing_sid = existing.get("session_id", "")
            has_existing_codex = existing_provider == "codex" and bool(existing_sid)
            existing_meta: CodexSessionMeta | None = None
            if existing_sid:
                for meta in all_metas:
                    if meta.session_id == existing_sid:
                        existing_meta = meta
                        break

            if (
                preferred_meta is not None
                and w.window_name == config.tmux_session_name
                and preferred_meta.session_id not in assigned_session_ids
            ):
                # Explicit resume target should win over stale/existing mapping
                # for the main ccbot window.
                chosen = preferred_meta

            if chosen is None:
                for meta in candidates:
                    if meta.session_id in assigned_session_ids:
                        continue
                    chosen = meta
                    break

            # If we couldn't resolve by cwd, keep previous mapping if still valid.
            if (
                chosen is None
                and existing_meta is not None
                and existing_meta.session_id not in assigned_session_ids
            ):
                chosen = existing_meta

            # Fallback: choose the newest unassigned rollout across all projects.
            # This handles "codex resume <id>" where pane cwd can differ from session cwd.
            if chosen is None:
                for meta in all_metas:
                    if meta.session_id in assigned_session_ids:
                        continue
                    chosen = meta
                    break

            # Keep previous raw mapping only when we failed to parse its meta
            # but file path still exists.
            if chosen is None and has_existing_codex:
                existing_fp = Path(existing.get("file_path", ""))
                if existing_fp.exists():
                    next_entries[key] = existing
                    assigned_session_ids.add(existing_sid)
                    continue

            if chosen is None:
                continue

            assigned_session_ids.add(chosen.session_id)
            next_entries[key] = {
                "session_id": chosen.session_id,
                "cwd": norm_cwd,
                "window_name": w.window_name,
                "provider": "codex",
                "file_path": str(chosen.file_path),
            }

        changed = False
        # Remove stale codex entries for this tmux session.
        stale_keys: list[str] = []
        for key, info in session_map.items():
            if not key.startswith(prefix):
                continue
            if key[len(prefix) :] not in live_wids:
                stale_keys.append(key)
                continue
            if isinstance(info, dict) and info.get("provider") == "codex":
                if key not in next_entries:
                    stale_keys.append(key)
        for key in stale_keys:
            del session_map[key]
            changed = True

        for key, info in next_entries.items():
            if session_map.get(key) != info:
                session_map[key] = info
                changed = True

        if changed:
            atomic_write_json(self.session_map_file, session_map)
            logger.debug("Codex session_map updated (%d entries)", len(next_entries))
        return changed


codex_session_mapper = CodexSessionMapper()
