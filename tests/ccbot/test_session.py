"""Tests for SessionManager pure dict operations."""

import pytest

from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestThreadBindings:
    def test_bind_and_get(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        assert mgr.get_window_for_thread(100, 1) == "@1"

    def test_bind_unbind_get_returns_none(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.unbind_thread(100, 1)
        assert mgr.get_window_for_thread(100, 1) is None

    def test_unbind_nonexistent_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.unbind_thread(100, 999) is None

    def test_get_thread_for_window(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 42, "@5")
        assert mgr.get_thread_for_window(100, "@5") == 42

    def test_iter_thread_bindings(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.bind_thread(100, 2, "@2")
        mgr.bind_thread(200, 3, "@3")
        result = set(mgr.iter_thread_bindings())
        assert result == {(100, 1, "@1"), (100, 2, "@2"), (200, 3, "@3")}


class TestResolveChatId:
    def test_with_stored_group_id(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 1, -999)
        assert mgr.resolve_chat_id(100, 1) == -999

    def test_without_group_id_falls_back(self, mgr: SessionManager) -> None:
        assert mgr.resolve_chat_id(100, 1) == 100

    def test_none_thread_id_falls_back(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 1, -999)
        assert mgr.resolve_chat_id(100) == 100


class TestWindowState:
    def test_get_creates_new(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@0")
        assert state.session_id == ""
        assert state.cwd == ""

    def test_get_returns_existing(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        assert mgr.get_window_state("@1").session_id == "abc"

    def test_clear_window_session(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        mgr.clear_window_session("@1")
        assert mgr.get_window_state("@1").session_id == ""


class TestResolveWindowForThread:
    def test_none_thread_id_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, None) is None

    def test_unbound_thread_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, 42) is None

    def test_bound_thread_returns_window(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 42, "@3")
        assert mgr.resolve_window_for_thread(100, 42) == "@3"


class TestDisplayNames:
    def test_get_display_name_fallback(self, mgr: SessionManager) -> None:
        """get_display_name returns window_id when no display name is set."""
        assert mgr.get_display_name("@99") == "@99"

    def test_set_and_get_display_name(self, mgr: SessionManager) -> None:
        mgr.set_display_name("@1", "myproject")
        assert mgr.get_display_name("@1") == "myproject"

    def test_set_display_name_update(self, mgr: SessionManager) -> None:
        mgr.set_display_name("@1", "old-name")
        mgr.set_display_name("@1", "new-name")
        assert mgr.get_display_name("@1") == "new-name"

    def test_bind_thread_sets_display_name(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", window_name="proj")
        assert mgr.get_display_name("@1") == "proj"

    def test_bind_thread_without_name_no_display(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        # No display name set, fallback to window_id
        assert mgr.get_display_name("@1") == "@1"


class TestIsWindowId:
    def test_valid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("@0") is True
        assert mgr._is_window_id("@12") is True
        assert mgr._is_window_id("@999") is True

    def test_invalid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("myproject") is False
        assert mgr._is_window_id("@") is False
        assert mgr._is_window_id("") is False
        assert mgr._is_window_id("@abc") is False


class TestFindUsersForSession:
    @staticmethod
    def _ws(session_id: str):
        from ccbot.session import WindowState

        return WindowState(session_id=session_id, cwd="/tmp")

    def test_returns_matching_users(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.window_states["@1"] = self._ws("sid-1")
        result = mgr.find_users_for_session("sid-1")
        assert result == [(100, "@1", 1)]

    def test_no_match_returns_empty(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.window_states["@1"] = self._ws("sid-1")
        assert mgr.find_users_for_session("sid-other") == []

    def test_multiple_users_same_session(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.bind_thread(200, 2, "@2")
        mgr.window_states["@1"] = self._ws("sid-shared")
        mgr.window_states["@2"] = self._ws("sid-shared")
        result = mgr.find_users_for_session("sid-shared")
        assert len(result) == 2
        assert {r[0] for r in result} == {100, 200}

    def test_ignores_windows_without_state(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        assert mgr.find_users_for_session("sid-1") == []


class TestParseSessionMap:
    def test_filters_by_prefix(self) -> None:
        from ccbot.session import parse_session_map

        raw = {
            "ccbot:win-a": {"session_id": "s1", "cwd": "/a"},
            "other:win-b": {"session_id": "s2", "cwd": "/b"},
        }
        result = parse_session_map(raw, "ccbot:")
        assert "win-a" in result
        assert "win-b" not in result

    def test_skips_empty_session_id(self) -> None:
        from ccbot.session import parse_session_map

        raw = {"ccbot:win-a": {"session_id": "", "cwd": "/a"}}
        assert parse_session_map(raw, "ccbot:") == {}

    def test_empty_input(self) -> None:
        from ccbot.session import parse_session_map

        assert parse_session_map({}, "ccbot:") == {}

    def test_extracts_cwd(self) -> None:
        from ccbot.session import parse_session_map

        raw = {"ccbot:win-a": {"session_id": "s1", "cwd": "/home/user/proj"}}
        result = parse_session_map(raw, "ccbot:")
        assert result["win-a"]["cwd"] == "/home/user/proj"

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param("a string", id="string-value"),
            pytest.param(42, id="int-value"),
            pytest.param(None, id="none-value"),
            pytest.param(["a", "list"], id="list-value"),
        ],
    )
    def test_non_dict_values_skipped(self, bad_value) -> None:
        from ccbot.session import parse_session_map

        raw = {
            "ccbot:good": {"session_id": "s1", "cwd": "/a"},
            "ccbot:bad": bad_value,
        }
        result = parse_session_map(raw, "ccbot:")
        assert "good" in result
        assert "bad" not in result
