"""Tests for SessionManager notification mode."""

import re

import pytest

from ccbot.handlers.callback_data import (
    NOTIFICATION_MODES,
    NOTIFY_MODE_ICONS,
    NOTIFY_MODE_LABELS,
)
from ccbot.session import SessionManager, WindowState


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestNotificationMode:
    def test_get_default_mode(self, mgr: SessionManager) -> None:
        assert mgr.get_notification_mode("@0") == "all"

    def test_get_mode_nonexistent_window(self, mgr: SessionManager) -> None:
        assert mgr.get_notification_mode("@999") == "all"

    def test_set_mode(self, mgr: SessionManager) -> None:
        mgr.set_notification_mode("@0", "muted")
        assert mgr.get_notification_mode("@0") == "muted"

    def test_set_mode_validates(self, mgr: SessionManager) -> None:
        with pytest.raises(ValueError, match="Invalid notification mode"):
            mgr.set_notification_mode("@0", "invalid_mode")

    @pytest.mark.parametrize(
        ("start", "expected"),
        [("all", "errors_only"), ("errors_only", "muted"), ("muted", "all")],
    )
    def test_cycle(self, mgr: SessionManager, start: str, expected: str) -> None:
        mgr.set_notification_mode("@0", start)
        assert mgr.cycle_notification_mode("@0") == expected
        assert mgr.get_notification_mode("@0") == expected

    def test_cycle_full_circle(self, mgr: SessionManager) -> None:
        mgr.cycle_notification_mode("@1")
        assert mgr.get_notification_mode("@1") == "errors_only"
        mgr.cycle_notification_mode("@1")
        assert mgr.get_notification_mode("@1") == "muted"
        mgr.cycle_notification_mode("@1")
        assert mgr.get_notification_mode("@1") == "all"

    def test_clear_window_resets_notification_mode(self, mgr: SessionManager) -> None:
        mgr.set_notification_mode("@0", "muted")
        mgr.clear_window_session("@0")
        assert mgr.get_notification_mode("@0") == "all"


class TestWindowStateSerialization:
    @pytest.mark.parametrize(
        ("mode", "expect_key"),
        [("all", False), ("errors_only", True), ("muted", True)],
    )
    def test_to_dict_notification_mode(self, mode: str, expect_key: bool) -> None:
        ws = WindowState(session_id="s1", cwd="/tmp", notification_mode=mode)
        d = ws.to_dict()
        if expect_key:
            assert d["notification_mode"] == mode
        else:
            assert "notification_mode" not in d

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"session_id": "s1", "cwd": "/tmp"}, "all"),
            (
                {"session_id": "s1", "cwd": "/tmp", "notification_mode": "errors_only"},
                "errors_only",
            ),
        ],
    )
    def test_from_dict(self, data: dict[str, str], expected: str) -> None:
        assert WindowState.from_dict(data).notification_mode == expected

    @pytest.mark.parametrize("mode", list(NOTIFICATION_MODES))
    def test_roundtrip(self, mode: str) -> None:
        ws = WindowState(session_id="s1", cwd="/tmp", notification_mode=mode)
        assert WindowState.from_dict(ws.to_dict()).notification_mode == mode


class TestNotificationModeConstants:
    def test_modes_match_icon_keys(self) -> None:
        assert set(NOTIFICATION_MODES) == set(NOTIFY_MODE_ICONS.keys())

    def test_modes_match_label_keys(self) -> None:
        assert set(NOTIFICATION_MODES) == set(NOTIFY_MODE_LABELS.keys())


class TestErrorKeywordsRegex:
    _ERROR_KEYWORDS_RE = re.compile(
        r"\b(?:error|exception|failed|traceback|stderr|assertion)\b", re.IGNORECASE
    )

    @pytest.mark.parametrize(
        "text",
        [
            "Error: something went wrong",
            "unhandled exception in module",
            "command failed with exit code 1",
            "Traceback (most recent call last):",
            "writing to stderr",
            "Assertion failed: x > 0",
            "FAILED test_foo",
            "an ERROR occurred",
        ],
    )
    def test_matches_error_keywords(self, text: str) -> None:
        assert self._ERROR_KEYWORDS_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "success",
            "all tests passed",
            "hello world",
            "no issues found",
            "errorless operation",
        ],
    )
    def test_no_match_on_normal_text(self, text: str) -> None:
        assert not self._ERROR_KEYWORDS_RE.search(text)
