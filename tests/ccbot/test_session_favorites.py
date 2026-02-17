"""Tests for SessionManager user directory favorites."""

from pathlib import Path

import pytest

from ccbot.session import SessionManager


def _resolved(path: str) -> str:
    return str(Path(path).resolve())


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestUserFavorites:
    @pytest.mark.parametrize("getter", ["get_user_starred", "get_user_mru"])
    def test_empty_default(self, mgr: SessionManager, getter: str) -> None:
        assert getattr(mgr, getter)(100) == []

    def test_update_mru_adds_to_front(self, mgr: SessionManager) -> None:
        mgr.update_user_mru(100, "/home/user/proj1")
        assert mgr.get_user_mru(100) == [_resolved("/home/user/proj1")]

    def test_update_mru_dedupes(self, mgr: SessionManager) -> None:
        mgr.update_user_mru(100, "/tmp/proj")
        mgr.update_user_mru(100, "/tmp/other")
        mgr.update_user_mru(100, "/tmp/proj")
        assert mgr.get_user_mru(100) == [
            _resolved("/tmp/proj"),
            _resolved("/tmp/other"),
        ]

    def test_update_mru_caps_at_five(self, mgr: SessionManager) -> None:
        for i in range(7):
            mgr.update_user_mru(100, f"/tmp/proj{i}")
        mru = mgr.get_user_mru(100)
        assert len(mru) == 5
        assert mru[0] == _resolved("/tmp/proj6")

    def test_update_mru_preserves_order(self, mgr: SessionManager) -> None:
        mgr.update_user_mru(100, "/tmp/a")
        mgr.update_user_mru(100, "/tmp/b")
        mgr.update_user_mru(100, "/tmp/c")
        assert mgr.get_user_mru(100) == [
            _resolved("/tmp/c"),
            _resolved("/tmp/b"),
            _resolved("/tmp/a"),
        ]

    def test_update_mru_resolves_relative_path(self, mgr: SessionManager) -> None:
        mgr.update_user_mru(100, "relative/proj")
        mru = mgr.get_user_mru(100)
        assert len(mru) == 1
        assert Path(mru[0]).is_absolute()
        assert mru[0] == _resolved("relative/proj")

    def test_toggle_star_adds(self, mgr: SessionManager) -> None:
        assert mgr.toggle_user_star(100, "/tmp/proj") is True
        assert _resolved("/tmp/proj") in mgr.get_user_starred(100)

    def test_toggle_star_removes(self, mgr: SessionManager) -> None:
        mgr.toggle_user_star(100, "/tmp/proj")
        assert mgr.toggle_user_star(100, "/tmp/proj") is False
        assert mgr.get_user_starred(100) == []

    def test_starred_multiple_paths(self, mgr: SessionManager) -> None:
        mgr.toggle_user_star(100, "/tmp/a")
        mgr.toggle_user_star(100, "/tmp/b")
        starred = mgr.get_user_starred(100)
        assert len(starred) == 2
        assert _resolved("/tmp/a") in starred
        assert _resolved("/tmp/b") in starred

    @pytest.mark.parametrize(
        ("setup", "getter"),
        [
            ("update_user_mru", "get_user_mru"),
            ("toggle_user_star", "get_user_starred"),
        ],
    )
    def test_independent_per_user(
        self, mgr: SessionManager, setup: str, getter: str
    ) -> None:
        getattr(mgr, setup)(100, "/tmp/user1")
        getattr(mgr, setup)(200, "/tmp/user2")
        assert getattr(mgr, getter)(100) != getattr(mgr, getter)(200)


class TestUserFavoritesPersistence:
    def test_roundtrip_via_do_save_and_load(self, tmp_path, monkeypatch) -> None:
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("ccbot.session.config.state_file", state_file)

        mgr = SessionManager()
        mgr.user_dir_favorites = {}
        mgr.update_user_mru(100, "/tmp/proj1")
        mgr.toggle_user_star(100, "/tmp/proj2")
        mgr._do_save_state()

        mgr2 = SessionManager()
        mgr2._load_state()
        assert mgr2.get_user_mru(100) == mgr.get_user_mru(100)
        assert mgr2.get_user_starred(100) == mgr.get_user_starred(100)
