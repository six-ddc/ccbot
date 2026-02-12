# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-02-12

Major rewrite as an independent fork of [six-ddc/ccbot](https://github.com/six-ddc/ccbot).

### Added

- Topic-based sessions: 1 topic = 1 tmux window = 1 Claude session
- Interactive UI for AskUserQuestion, ExitPlanMode, and Permission prompts
- Sessions dashboard with per-session status and kill buttons
- Message history with paginated browsing (newest first)
- Auto-discovery of Claude Code skills and custom commands in Telegram menu
- Hook-based session tracking (SessionStart hook writes session_map.json)
- Per-user message queue with FIFO ordering and message merging
- Rate limiting (1.1s minimum interval per user)
- Multi-instance support via CCBOT_GROUP_ID and CCBOT_INSTANCE_NAME
- Auto-topic creation for manually created tmux windows (including cold-start)
- Fresh/Continue/Resume recovery flows for dead sessions
- /resume command to browse and resume past sessions
- Directory browser for new topic session creation
- MarkdownV2 output with automatic plain text fallback
- Terminal screenshot rendering (ANSI color support)
- Status line polling with spinner and working text
- Expandable quote formatting for thinking content
- Persistent state (thread bindings, read offsets survive restarts)
- Topic emoji status updates reflecting session state
- Configurable config directory via CCBOT_DIR env var

### Changed

- Internal routing keyed by tmux window ID instead of window name
- Python 3.14 required (up from 3.12)
- Replaced broad exception handlers with specific types
- Normalized variable naming (full names instead of short aliases)
- Enabled C901, PLR, N ruff quality gate rules

### Removed

- Non-topic mode (active_sessions, /list, General topic routing)
- Message truncation at parse layer (splitting only at send layer)

## [0.1.0] - 2026-02-07

Initial release by [six-ddc](https://github.com/six-ddc).

[Unreleased]: https://github.com/alexei-led/ccbot/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/alexei-led/ccbot/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/alexei-led/ccbot/releases/tag/v0.1.0
