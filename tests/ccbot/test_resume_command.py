"""Tests for /resume command (TASK-011)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from ccbot.handlers.callback_data import (
    CB_RESUME_CANCEL,
    CB_RESUME_PAGE,
    CB_RESUME_PICK,
)
from ccbot.handlers.resume_command import (
    ResumeEntry,
    _build_resume_keyboard,
    handle_resume_command_callback,
    resume_command,
    scan_all_sessions,
)
from ccbot.handlers.user_state import RESUME_SESSIONS

_RC = "ccbot.handlers.resume_command"


def _make_update(
    *,
    chat_id: int = -100999,
    user_id: int = 100,
    thread_id: int = 42,
    text: str = "/resume",
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    msg = MagicMock()
    msg.text = text
    msg.message_thread_id = thread_id
    msg.chat.type = "supergroup"
    msg.chat.is_forum = True
    msg.is_topic_message = True
    update.message = msg
    update.callback_query = None
    return update


def _make_callback_update(
    *,
    chat_id: int = -100999,
    user_id: int = 100,
    thread_id: int = 42,
    data: str = "",
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    query = AsyncMock()
    query.data = data
    query.message = MagicMock()
    query.message.chat.type = "supergroup"
    query.message.chat.id = chat_id
    query.message.message_thread_id = thread_id
    query.message.chat.is_forum = True
    query.message.is_topic_message = True
    update.callback_query = query
    update.message = None
    return update


def _make_context(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot = AsyncMock()
    return ctx


class TestScanAllSessions:
    def test_returns_sessions_from_index(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-1.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": "/tmp/myproj",
            "entries": [
                {
                    "sessionId": "sess-1",
                    "fullPath": str(session_file),
                    "projectPath": "/tmp/myproj",
                    "summary": "Fix the bug",
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert len(result) == 1
        assert result[0].session_id == "sess-1"
        assert result[0].summary == "Fix the bug"
        assert result[0].cwd == "/tmp/myproj"

    def test_returns_empty_when_projects_path_missing(self, tmp_path) -> None:
        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = tmp_path / "nonexistent"
            result = scan_all_sessions()

        assert result == []

    def test_deduplicates_by_session_id(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"

        for name in ("proj-a", "proj-b"):
            proj_dir = projects_path / name
            proj_dir.mkdir(parents=True)
            sf = proj_dir / "sess-dup.jsonl"
            sf.write_text('{"type":"summary"}\n')
            index = {
                "originalPath": f"/tmp/{name}",
                "entries": [
                    {
                        "sessionId": "sess-dup",
                        "fullPath": str(sf),
                        "projectPath": f"/tmp/{name}",
                        "summary": f"From {name}",
                    }
                ],
            }
            (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert len(result) == 1

    def test_skips_missing_session_files(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        index = {
            "originalPath": "/tmp/myproj",
            "entries": [
                {
                    "sessionId": "sess-gone",
                    "fullPath": str(proj_dir / "nonexistent.jsonl"),
                    "projectPath": "/tmp/myproj",
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert result == []

    def test_uses_session_id_as_summary_fallback(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-abc123.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": "/tmp/myproj",
            "entries": [
                {
                    "sessionId": "sess-abc123",
                    "fullPath": str(session_file),
                    "projectPath": "/tmp/myproj",
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert len(result) == 1
        assert result[0].summary == "sess-abc123"

    def test_sorted_by_mtime_descending(self, tmp_path) -> None:
        import time

        projects_path = tmp_path / "projects"
        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        old_file = proj_dir / "sess-old.jsonl"
        old_file.write_text('{"type":"summary"}\n')
        time.sleep(0.05)

        new_file = proj_dir / "sess-new.jsonl"
        new_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": "/tmp/myproj",
            "entries": [
                {
                    "sessionId": "sess-old",
                    "fullPath": str(old_file),
                    "projectPath": "/tmp/myproj",
                    "summary": "Old session",
                },
                {
                    "sessionId": "sess-new",
                    "fullPath": str(new_file),
                    "projectPath": "/tmp/myproj",
                    "summary": "New session",
                },
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert len(result) == 2
        assert result[0].session_id == "sess-new"
        assert result[1].session_id == "sess-old"

    def test_scans_multiple_projects(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"

        for i, name in enumerate(("proj-a", "proj-b")):
            proj_dir = projects_path / name
            proj_dir.mkdir(parents=True)
            sf = proj_dir / f"sess-{i}.jsonl"
            sf.write_text('{"type":"summary"}\n')
            index = {
                "originalPath": f"/tmp/{name}",
                "entries": [
                    {
                        "sessionId": f"sess-{i}",
                        "fullPath": str(sf),
                        "projectPath": f"/tmp/{name}",
                        "summary": f"Session {i}",
                    }
                ],
            }
            (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert len(result) == 2
        ids = {r.session_id for r in result}
        assert ids == {"sess-0", "sess-1"}

    def test_skips_invalid_json(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "sessions-index.json").write_text("not valid json{{{")

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_all_sessions()

        assert result == []


class TestBuildResumeKeyboard:
    def _sessions(self, count: int = 3) -> list[dict[str, str]]:
        return [
            {"session_id": f"sess-{i}", "summary": f"Session {i}", "cwd": "/tmp/proj"}
            for i in range(count)
        ]

    def test_session_buttons(self) -> None:
        sessions = self._sessions(2)
        kb = _build_resume_keyboard(sessions)
        # First row: project header, then 2 session rows, then nav row
        assert len(kb.inline_keyboard) == 4
        assert kb.inline_keyboard[1][0].callback_data == f"{CB_RESUME_PICK}0"
        assert kb.inline_keyboard[2][0].callback_data == f"{CB_RESUME_PICK}1"

    def test_project_header(self) -> None:
        sessions = self._sessions(1)
        kb = _build_resume_keyboard(sessions)
        header = kb.inline_keyboard[0][0]
        assert "proj" in header.text
        assert header.callback_data == "noop"

    def test_cancel_button_present(self) -> None:
        sessions = self._sessions(1)
        kb = _build_resume_keyboard(sessions)
        nav_row = kb.inline_keyboard[-1]
        cancel = [b for b in nav_row if b.callback_data == CB_RESUME_CANCEL]
        assert len(cancel) == 1

    def test_no_prev_on_first_page(self) -> None:
        sessions = self._sessions(1)
        kb = _build_resume_keyboard(sessions, page=0)
        nav_row = kb.inline_keyboard[-1]
        prev_btns = [b for b in nav_row if CB_RESUME_PAGE in (b.callback_data or "")]
        assert len(prev_btns) == 0

    def test_next_button_on_first_page(self) -> None:
        sessions = self._sessions(10)
        kb = _build_resume_keyboard(sessions, page=0)
        nav_row = kb.inline_keyboard[-1]
        next_btns = [
            b
            for b in nav_row
            if b.callback_data and b.callback_data.startswith(CB_RESUME_PAGE)
        ]
        assert len(next_btns) == 1
        assert "Next" in next_btns[0].text

    def test_prev_button_on_second_page(self) -> None:
        sessions = self._sessions(10)
        kb = _build_resume_keyboard(sessions, page=1)
        nav_row = kb.inline_keyboard[-1]
        prev_btns = [b for b in nav_row if "Prev" in b.text]
        assert len(prev_btns) == 1

    def test_callback_data_truncated_to_64(self) -> None:
        sessions = [
            {"session_id": f"sess-{'x' * 60}", "summary": "Long", "cwd": "/tmp/proj"}
        ]
        kb = _build_resume_keyboard(sessions)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    assert len(btn.callback_data) <= 64

    def test_grouped_by_cwd(self) -> None:
        sessions = [
            {"session_id": "s1", "summary": "A", "cwd": "/proj/a"},
            {"session_id": "s2", "summary": "B", "cwd": "/proj/b"},
        ]
        kb = _build_resume_keyboard(sessions)
        headers = [
            row[0] for row in kb.inline_keyboard if row[0].callback_data == "noop"
        ]
        assert len(headers) == 2


class TestResumeCommand:
    @patch(f"{_RC}.scan_all_sessions")
    @patch(f"{_RC}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_shows_session_picker(
        self,
        _mock_thread_id: MagicMock,
        mock_safe_reply: AsyncMock,
        mock_scan: MagicMock,
    ) -> None:
        mock_scan.return_value = [
            ResumeEntry("sess-1", "Fix bug", "/tmp/proj"),
            ResumeEntry("sess-2", "Add tests", "/tmp/proj"),
        ]

        update = _make_update()
        user_data: dict = {}
        ctx = _make_context(user_data)

        await resume_command(update, ctx)

        mock_safe_reply.assert_called_once()
        assert "Select a session" in mock_safe_reply.call_args.args[1]
        assert RESUME_SESSIONS in user_data
        assert len(user_data[RESUME_SESSIONS]) == 2

    @patch(f"{_RC}.scan_all_sessions")
    @patch(f"{_RC}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_no_sessions_shows_message(
        self,
        _mock_thread_id: MagicMock,
        mock_safe_reply: AsyncMock,
        mock_scan: MagicMock,
    ) -> None:
        mock_scan.return_value = []

        update = _make_update()
        ctx = _make_context()

        await resume_command(update, ctx)

        mock_safe_reply.assert_called_once()
        assert "No past sessions" in mock_safe_reply.call_args.args[1]

    @patch(f"{_RC}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=None)
    async def test_no_topic_rejected(
        self,
        _mock_thread_id: MagicMock,
        mock_safe_reply: AsyncMock,
    ) -> None:
        update = _make_update()
        ctx = _make_context()

        await resume_command(update, ctx)

        mock_safe_reply.assert_called_once()
        assert "named topic" in mock_safe_reply.call_args.args[1]

    async def test_no_message_returns_early(self) -> None:
        update = MagicMock()
        update.message = None
        ctx = _make_context()

        await resume_command(update, ctx)


class TestResumePickCallback:
    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_creates_window_with_resume(
        self,
        _mock_thread_id: MagicMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_tm.create_window.assert_called_once_with(
            "/tmp/proj", claude_args="--resume sess-abc"
        )
        mock_sm.bind_thread.assert_called_once_with(
            100, 42, "@5", window_name="project"
        )

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_unbinds_old_window(
        self,
        _mock_thread_id: MagicMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_sm.unbind_thread.assert_called_once_with(100, 42)

    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_invalid_cwd_fails(
        self,
        _mock_thread_id: MagicMock,
        mock_safe_edit: AsyncMock,
    ) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/gone"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "no longer exists" in mock_safe_edit.call_args.args[1].lower()
        assert RESUME_SESSIONS not in user_data

    async def test_pick_invalid_index_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PICK}99")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "test", "cwd": "/tmp"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.get_thread_id", return_value=42):
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert (
            "invalid"
            in query.answer.call_args.kwargs.get(
                "text",
                query.answer.call_args.args[0] if query.answer.call_args.args else "",
            ).lower()
        )

    async def test_pick_no_sessions_stored_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        ctx = _make_context({})
        query = update.callback_query

        with patch(f"{_RC}.get_thread_id", return_value=42):
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()

    async def test_pick_no_topic_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "test", "cwd": "/tmp"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.get_thread_id", return_value=None):
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_second_session(
        self,
        _mock_thread_id: MagicMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RESUME_PICK}1")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
                {"session_id": "sess-def", "summary": "Add tests", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_tm.create_window.assert_called_once_with(
            "/tmp/proj", claude_args="--resume sess-def"
        )

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_sets_group_chat_id(
        self,
        _mock_thread_id: MagicMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_sm.set_group_chat_id.assert_called_once_with(100, 42, -100999)

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_clears_resume_state(
        self,
        _mock_thread_id: MagicMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        assert RESUME_SESSIONS not in user_data

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.get_thread_id", return_value=42)
    async def test_pick_create_window_failure(
        self,
        _mock_thread_id: MagicMock,
        mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(False, "Tmux error", None, None)
        )

        update = _make_callback_update(data=f"{CB_RESUME_PICK}0")
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "Fix bug", "cwd": "/tmp/proj"},
            ],
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "Tmux error" in mock_safe_edit.call_args.args[1]
        assert RESUME_SESSIONS not in user_data

    async def test_pick_invalid_value_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PICK}notanumber")
        ctx = _make_context({})
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()


class TestResumePageCallback:
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_page_shows_sessions(
        self,
        mock_safe_edit: AsyncMock,
    ) -> None:
        sessions = [
            {"session_id": f"sess-{i}", "summary": f"Session {i}", "cwd": "/tmp/proj"}
            for i in range(10)
        ]
        user_data: dict = {RESUME_SESSIONS: sessions}
        update = _make_callback_update(data=f"{CB_RESUME_PAGE}1")
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "Select a session" in mock_safe_edit.call_args.args[1]

    async def test_page_invalid_number_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PAGE}abc")
        ctx = _make_context({})
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()

    async def test_page_no_sessions_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RESUME_PAGE}0")
        ctx = _make_context({})
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()


class TestResumeCancelCallback:
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_cancel_clears_state(
        self,
        mock_safe_edit: AsyncMock,
    ) -> None:
        user_data: dict = {
            RESUME_SESSIONS: [
                {"session_id": "sess-abc", "summary": "test", "cwd": "/tmp"},
            ],
        }
        update = _make_callback_update(data=CB_RESUME_CANCEL)
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        assert RESUME_SESSIONS not in user_data
        mock_safe_edit.assert_called_once()
        assert "cancelled" in mock_safe_edit.call_args.args[1].lower()

    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_cancel_answers_query(
        self,
        _mock_safe_edit: AsyncMock,
    ) -> None:
        update = _make_callback_update(data=CB_RESUME_CANCEL)
        ctx = _make_context({})
        query = update.callback_query

        await handle_resume_command_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once_with("Cancelled")
