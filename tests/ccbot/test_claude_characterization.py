"""Characterization tests locking Claude-specific behaviors.

These tests document current Claude Code integration behavior before
multi-provider refactoring begins. They serve as a safety net — if any
test breaks during extraction, it signals an unintended behavior change.

Only tests behaviors NOT already covered by existing unit tests.
"""

import pytest

from ccbot.cc_commands import CC_BUILTINS
from ccbot.hook import UUID_RE
from ccbot.terminal_parser import (
    STATUS_SPINNERS,
    UI_PATTERNS,
    extract_interactive_content,
    parse_status_line,
)
from ccbot.providers.base import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START
from ccbot.transcript_parser import TranscriptParser

# ── Hook payload format ──────────────────────────────────────────────────


class TestClaudeHookPayloadFormat:
    def test_uuid_validation_accepts_valid(self) -> None:
        assert UUID_RE.match("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "not-a-uuid",
            "A1B2C3D4-E5F6-7890-ABCD-EF1234567890",  # uppercase rejected
            "a1b2c3d4e5f6-7890-abcd-ef1234567890",  # missing dash
            "a1b2c3d4-e5f6-7890-abcd-ef123456789",  # too short
        ],
    )
    def test_uuid_validation_rejects_invalid(self, invalid: str) -> None:
        assert UUID_RE.match(invalid) is None

    def test_uuid_regex_exact_pattern(self) -> None:
        expected = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert UUID_RE.pattern == expected


# ── Transcript format ────────────────────────────────────────────────────


class TestClaudeTranscriptFormat:
    def test_expquote_sentinels(self) -> None:
        assert EXPANDABLE_QUOTE_START == "\x02EXPQUOTE_START\x02"
        assert EXPANDABLE_QUOTE_END == "\x02EXPQUOTE_END\x02"

    def test_tool_pair_carry_over(self, make_jsonl_entry, make_tool_use_block) -> None:
        entries = [make_jsonl_entry("assistant", [make_tool_use_block("t1", "Read")])]
        _, pending = TranscriptParser.parse_entries(entries, {})
        assert "t1" in pending

    def test_tool_pair_resolved_on_result(
        self, make_jsonl_entry, make_tool_use_block, make_tool_result_block
    ) -> None:
        entries = [
            make_jsonl_entry("assistant", [make_tool_use_block("t1", "Read")]),
            make_jsonl_entry("user", [make_tool_result_block("t1", "file contents")]),
        ]
        _, pending = TranscriptParser.parse_entries(entries, {})
        assert "t1" not in pending

    def test_exit_plan_mode_emits_plan_text(self, make_jsonl_entry) -> None:
        plan_text = "## Plan\n1. Do thing\n2. Do other thing"
        entries = [
            make_jsonl_entry(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": "epm1",
                        "name": "ExitPlanMode",
                        "input": {"plan": plan_text},
                    }
                ],
            )
        ]
        result, _ = TranscriptParser.parse_entries(entries, {})
        texts = [e.text for e in result]
        assert plan_text in texts
        assert any(
            e.content_type == "tool_use" and e.tool_name == "ExitPlanMode"
            for e in result
        )

    def test_summary_entries_skipped(self, make_jsonl_entry) -> None:
        entries = [
            {"type": "summary", "summary": "conversation summary"},
            make_jsonl_entry("assistant", [{"type": "text", "text": "hello"}]),
        ]
        result, _ = TranscriptParser.parse_entries(entries, {})
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_file_history_snapshot_skipped(self) -> None:
        entries = [
            {"type": "file-history-snapshot", "data": {}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hi"}]},
            },
        ]
        result, _ = TranscriptParser.parse_entries(entries, {})
        assert len(result) == 1


# ── Terminal signatures ──────────────────────────────────────────────────


class TestClaudeTerminalSignatures:
    def test_exact_spinner_charset(self) -> None:
        expected = frozenset(["·", "✻", "✽", "✶", "✳", "✢"])
        assert expected == STATUS_SPINNERS

    def test_all_ui_types_defined(self) -> None:
        names = {p.name for p in UI_PATTERNS}
        expected = {
            "ExitPlanMode",
            "AskUserQuestion",
            "PermissionPrompt",
            "RestoreCheckpoint",
            "Settings",
            "SelectModel",
        }
        assert names == expected

    def test_two_separator_layout_recognized(self) -> None:
        sep = "─" * 30
        pane = f"output\n✻ Reading file\n{sep}\n❯ \n{sep}\n"
        result = parse_status_line(pane)
        assert result is not None
        assert "Reading file" in result

    def test_interactive_ui_detection(self) -> None:
        pane = (
            "  Would you like to proceed?\n"
            "  ─────────────────────────────────\n"
            "  Yes     No\n"
            "  ─────────────────────────────────\n"
            "  ctrl-g to edit in vim\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "ExitPlanMode"


# ── Command discovery ────────────────────────────────────────────────────


class TestClaudeCommandDiscovery:
    def test_cc_builtins_exact_set(self) -> None:
        expected = {"clear", "compact", "cost", "help", "memory", "model"}
        assert set(CC_BUILTINS.keys()) == expected


# ── Resume and recovery ─────────────────────────────────────────────────


class TestClaudeResumeAndRecovery:
    def test_sessions_index_format(self) -> None:
        index = {
            "originalPath": "/home/user/project",
            "entries": [
                {
                    "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "fullPath": "/tmp/session.jsonl",
                    "summary": "Test session",
                    "projectPath": "/home/user/project",
                }
            ],
        }
        entry = index["entries"][0]
        assert UUID_RE.match(entry["sessionId"])
        assert "fullPath" in entry
        assert "projectPath" in entry
