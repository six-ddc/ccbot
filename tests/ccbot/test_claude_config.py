"""Tests for claude_config.ensure_trusted_directory."""

import json
import subprocess

import pytest

import ccbot.claude_config as cc


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / ".claude.json"
    monkeypatch.setattr(cc, "claude_config_file", lambda: path)
    # No git: key is the directory itself
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 128, stdout="", stderr=""),
    )
    return path


class TestEnsureTrustedDirectory:
    def test_adds_entry_and_keeps_other_keys(self, cfg, tmp_path):
        cfg.write_text(json.dumps({"theme": "dark", "projects": {"/x": {"a": 1}}}))
        cfg.chmod(0o600)
        work = tmp_path / "proj"
        work.mkdir()
        assert cc.ensure_trusted_directory(work) is True
        data = json.loads(cfg.read_text())
        assert data["theme"] == "dark"
        assert data["projects"]["/x"] == {"a": 1}
        assert data["projects"][str(work.resolve())] == {"hasTrustDialogAccepted": True}
        assert cfg.stat().st_mode & 0o777 == 0o600

    def test_noop_when_already_trusted(self, cfg, tmp_path):
        work = tmp_path / "proj"
        work.mkdir()
        cfg.write_text(
            json.dumps(
                {"projects": {str(work.resolve()): {"hasTrustDialogAccepted": True}}}
            )
        )
        before = cfg.read_text()
        assert cc.ensure_trusted_directory(work) is True
        assert cfg.read_text() == before

    def test_existing_entry_gets_flag(self, cfg, tmp_path):
        work = tmp_path / "proj"
        work.mkdir()
        key = str(work.resolve())
        cfg.write_text(
            json.dumps({"projects": {key: {"hasTrustDialogAccepted": False, "k": 2}}})
        )
        assert cc.ensure_trusted_directory(work) is True
        data = json.loads(cfg.read_text())
        assert data["projects"][key] == {"hasTrustDialogAccepted": True, "k": 2}

    def test_missing_file_untouched(self, cfg, tmp_path):
        assert cc.ensure_trusted_directory(tmp_path) is False
        assert not cfg.exists()

    def test_malformed_file_untouched(self, cfg, tmp_path):
        cfg.write_text("{not json")
        assert cc.ensure_trusted_directory(tmp_path) is False
        assert cfg.read_text() == "{not json"

    def test_git_root_used_as_key(self, cfg, tmp_path, monkeypatch):
        cfg.write_text(json.dumps({"projects": {}}))
        root = tmp_path / "repo"
        sub = root / "src" / "pkg"
        sub.mkdir(parents=True)
        monkeypatch.setattr(
            cc.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, stdout=f"{root}\n", stderr=""
            ),
        )
        assert cc.ensure_trusted_directory(sub) is True
        data = json.loads(cfg.read_text())
        assert list(data["projects"]) == [str(root.resolve())]
