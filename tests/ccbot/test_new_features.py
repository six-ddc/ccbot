"""Tests for features added during the continuous improvement cycle (cycles 1-17).

Covers: rich progress status, config new fields, document upload constants,
auto-name skip patterns, and context extraction helpers.
"""

import pytest

from ccbot.config import Config
from ccbot.session_monitor import _format_tool_status


# --- Rich Progress Status (_format_tool_status) ---


class TestFormatToolStatus:
    def test_read_with_path(self):
        result = _format_tool_status("Read", "**Read**(src/auth.py)")
        assert "Читаю" in result
        assert "src/auth.py" in result

    def test_write_with_path(self):
        result = _format_tool_status("Write", "**Write**(config.ts)")
        assert "Пишу" in result
        assert "config.ts" in result

    def test_edit_with_path(self):
        result = _format_tool_status("Edit", "**Edit**(main.py)")
        assert "Редактирую" in result
        assert "main.py" in result

    def test_bash_with_command(self):
        result = _format_tool_status("Bash", "**Bash**(`npm test`)")
        assert "npm test" in result

    def test_grep_with_pattern(self):
        result = _format_tool_status("Grep", "**Grep**(authentication)")
        assert "Ищу" in result
        assert "authentication" in result

    def test_glob(self):
        result = _format_tool_status("Glob", "**Glob**(*.py)")
        assert "файлы" in result.lower() or "*.py" in result

    def test_agent(self):
        result = _format_tool_status("Agent", "**Agent**(code review)")
        assert "Агент" in result

    def test_mcp_tool(self):
        result = _format_tool_status("mcp__serena__find_symbol", "")
        assert "find_symbol" in result

    def test_thinking(self):
        result = _format_tool_status("thinking", "")
        assert "Думаю" in result

    def test_unknown_tool(self):
        result = _format_tool_status("CustomTool", "")
        assert "CustomTool" in result

    def test_none_tool(self):
        result = _format_tool_status(None, "")
        assert "Работаю" in result

    def test_no_args_fallback(self):
        result = _format_tool_status("Read", "")
        assert "Читаю" in result

    def test_long_arg_truncation(self):
        long_path = "a" * 100
        result = _format_tool_status("Read", f"**Read**({long_path})")
        assert len(result) < 150
        assert "…" in result

    def test_websearch(self):
        result = _format_tool_status("WebSearch", "**WebSearch**(query)")
        assert "интернете" in result.lower() or "query" in result


# --- Config New Fields ---


@pytest.fixture
def _base_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("ALLOWED_USERS", "12345")
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))


@pytest.mark.usefixtures("_base_env")
class TestConfigNewFields:
    def test_idle_reminder_default(self):
        cfg = Config()
        assert cfg.idle_reminder_seconds == 120

    def test_idle_reminder_custom(self, monkeypatch):
        monkeypatch.setenv("CCBOT_IDLE_REMINDER_SECONDS", "300")
        cfg = Config()
        assert cfg.idle_reminder_seconds == 300

    def test_idle_reminder_disabled(self, monkeypatch):
        monkeypatch.setenv("CCBOT_IDLE_REMINDER_SECONDS", "0")
        cfg = Config()
        assert cfg.idle_reminder_seconds == 0

    def test_input_batch_default(self):
        cfg = Config()
        assert cfg.input_batch_seconds == 1.5

    def test_input_batch_custom(self, monkeypatch):
        monkeypatch.setenv("CCBOT_INPUT_BATCH_SECONDS", "3.0")
        cfg = Config()
        assert cfg.input_batch_seconds == 3.0

    def test_input_batch_disabled(self, monkeypatch):
        monkeypatch.setenv("CCBOT_INPUT_BATCH_SECONDS", "0")
        cfg = Config()
        assert cfg.input_batch_seconds == 0.0

    def test_auto_restart_default(self):
        cfg = Config()
        assert cfg.auto_restart is True

    def test_auto_restart_disabled(self, monkeypatch):
        monkeypatch.setenv("CCBOT_AUTO_RESTART", "false")
        cfg = Config()
        assert cfg.auto_restart is False

    def test_long_response_threshold_default(self):
        cfg = Config()
        assert cfg.long_response_threshold == 6000

    def test_long_response_threshold_custom(self, monkeypatch):
        monkeypatch.setenv("CCBOT_LONG_RESPONSE_THRESHOLD", "10000")
        cfg = Config()
        assert cfg.long_response_threshold == 10000

    def test_file_retention_default(self):
        cfg = Config()
        assert cfg.file_retention_days == 7

    def test_file_retention_disabled(self, monkeypatch):
        monkeypatch.setenv("CCBOT_FILE_RETENTION_DAYS", "0")
        cfg = Config()
        assert cfg.file_retention_days == 0


# --- Document Upload Constants ---


class TestDocumentUploadConstants:
    def test_receivable_exts_contains_common_code(self):
        from ccbot.bot import _RECEIVABLE_DOC_EXTS

        for ext in (".py", ".js", ".ts", ".json", ".yaml", ".md", ".txt"):
            assert ext in _RECEIVABLE_DOC_EXTS, f"{ext} missing from receivable exts"

    def test_receivable_exts_no_executables(self):
        from ccbot.bot import _RECEIVABLE_DOC_EXTS

        for ext in (".exe", ".bat", ".com", ".msi", ".app", ".dmg"):
            assert ext not in _RECEIVABLE_DOC_EXTS, f"{ext} should not be receivable"

    def test_sensitive_upload_names(self):
        from ccbot.bot import _SENSITIVE_UPLOAD_NAMES

        assert ".env" in _SENSITIVE_UPLOAD_NAMES
        assert "credentials" in _SENSITIVE_UPLOAD_NAMES
        assert "id_rsa" in _SENSITIVE_UPLOAD_NAMES


# --- Auto-name Skip Patterns ---


class TestAutoNamePatterns:
    def test_greetings_skipped(self):
        from ccbot.bot import _SKIP_AUTONAME_PATTERNS

        for greeting in ("hi", "hello", "привет", "ok"):
            assert greeting in _SKIP_AUTONAME_PATTERNS, f"{greeting} should be skipped"

    def test_normal_messages_not_skipped(self):
        from ccbot.bot import _SKIP_AUTONAME_PATTERNS

        for msg in ("fix bug in auth", "review this code", "напиши тест"):
            assert msg not in _SKIP_AUTONAME_PATTERNS
