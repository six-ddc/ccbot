# ccbot Improvement Plan: Multi-Group, Multi-Instance, and Tmux Sync

Complete remaining spec tasks, fix spec inconsistencies, and add missing tasks for robustness around the core use cases: multiple groups, multiple instances, and tmux-to-telegram topic syncing.

## Context

- Files involved: `src/ccbot/bot.py`, `src/ccbot/session.py`, `src/ccbot/handlers/*.py`, `src/ccbot/cc_commands.py`, `src/ccbot/tmux_manager.py`, `.spec/tasks/TASK-024-refactor-text-handler.md`, `pyproject.toml`
- Related patterns: spec-driven development (`.spec/`), hook-based session tracking, topic-only architecture
- Dependencies: None external; all work builds on existing codebase

## Approach

- **Testing approach**: Regular (code first, then tests) for feature work; existing tests must stay green throughout
- Complete each task fully before moving to the next
- Use `/spec:work` for individual task execution where spec tasks already exist
- Create new spec tasks (via `/spec:new`) for gaps identified below before implementing
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task** (`make check`)

## Current State

- 31 tasks across 7 epics; 10 done (+ TASK-024 done in git but spec says todo)
- Multi-instance (EPIC-001): FULLY DONE - group filtering, config, docs all complete
- Auto-sync of manually created tmux windows to topics: ALREADY WORKS (hook -> session_monitor -> \_handle_new_window in bot.py)
- Cold-start gap: auto-topic creation requires at least one existing binding (no known groups otherwise)
- All 359 tests pass, lint/typecheck clean
- EPIC-006 (Python 3.14 cleanup) is 4/7 done, 3 remaining tasks are sequential

## Issues Found

1. TASK-024 spec status is "todo" but code is done and committed (b1a90d6). Needs spec update.
2. Auto-topic creation for new tmux windows has a cold-start problem: if no user has any bindings yet, the bot doesn't know which group chat to create topics in.
3. No task covers multi-group auto-topic creation when CCBOT_GROUP_ID is set (the bot already knows the target group from config but `_handle_new_window` doesn't use it).
4. No task for validating the full manual-tmux-to-topic flow end-to-end.

## What's NOT Needed (Already Works)

- Multi-instance filtering (EPIC-001 complete)
- Basic auto-topic creation for new tmux windows (works when bindings exist)
- Session monitoring and change detection
- Hook-based session tracking

---

## Phase A: Fix Spec + Unblock (housekeeping)

### Task 1: Fix TASK-024 spec status

**Files:**

- Modify: `.spec/tasks/TASK-024-refactor-text-handler.md`

**Steps:**

- [x] Update TASK-024 frontmatter status from `todo` to `done`
- [x] Verify no other spec files reference TASK-024 as blocking

---

## Phase B: Tmux Sync Improvements (core user request)

### Task 2: NEW TASK - Fix cold-start auto-topic creation

When `CCBOT_GROUP_ID` is configured, use it as the target group for auto-topic creation even when no bindings exist. This eliminates the cold-start gap for the primary multi-instance use case.

**Files:**

- Modify: `src/ccbot/bot.py` (`_handle_new_window` method)
- Modify or create: tests for the cold-start scenario

**Steps:**

- [x] Create spec task via `/spec:new` for cold-start fix
- [x] In `_handle_new_window`, when no existing bindings found, fall back to `CCBOT_GROUP_ID` from config
- [x] Add guard: skip auto-topic if no group ID available (neither from bindings nor config)
- [x] Write tests for: cold-start with CCBOT_GROUP_ID set, cold-start without CCBOT_GROUP_ID, normal flow unchanged
- [x] Run `make check` - must pass

### Task 3: NEW TASK - End-to-end test for manual tmux window sync

Verify the full flow: hook writes session_map -> monitor detects -> topic created -> bindings established.

**Files:**

- Create: `tests/ccbot/test_new_window_sync.py`

**Steps:**

- [x] Create spec task via `/spec:new` for e2e sync test
- [x] Write integration test covering: session_map update -> monitor poll -> \_handle_new_window -> topic creation -> binding verification
- [x] Test with and without `CCBOT_GROUP_ID` set
- [x] Run `make check` - must pass

---

## Phase C: Continue EPIC-006 (Python 3.14 cleanup - 3 remaining)

### Task 4: TASK-025 - Normalize naming and centralize state handling

Replace short aliases (`wid` -> `window_id`, etc.), centralize user-data key constants.

**Files:**

- Modify: `src/ccbot/handlers/*.py`, `src/ccbot/session.py`

**Steps:**

- [x] Run `/spec:work` on TASK-025
- [x] Replace all short variable aliases with full names
- [x] Centralize user-data key constants
- [x] Update all affected tests
- [x] Run `make check` - must pass

### Task 5: TASK-026 - Harden exceptions and logging

Replace broad `except Exception` with specific types, standardize logging.

**Files:**

- Modify: all `src/ccbot/*.py`

**Steps:**

- [x] Run `/spec:work` on TASK-026
- [x] Audit and replace broad exception handlers
- [x] Standardize logging format/levels
- [x] Run `make check` - must pass

### Task 6: TASK-027 - Quality gates Stage B+C

Enable C901, PLR0911, PLR0912, PLR0915, N ruff rules, fix violations.

**Files:**

- Modify: `pyproject.toml`, all source files with violations

**Steps:**

- [ ] Run `/spec:work` on TASK-027
- [ ] Enable rules incrementally, fixing violations
- [ ] Run `make check` - must pass

---

## Phase D: Complete EPIC-002 + EPIC-003 (command + recovery)

### Task 7: TASK-008 - Forward discovered CC commands to tmux

**Files:**

- Modify: `src/ccbot/bot.py`, `src/ccbot/cc_commands.py`

**Steps:**

- [ ] Run `/spec:work` on TASK-008
- [ ] Verify/fix forward_command_handler for skill and custom commands
- [ ] Write tests
- [ ] Run `make check` - must pass

### Task 8: TASK-010 - Fresh/Continue/Resume recovery flows

**Files:**

- Modify: `src/ccbot/handlers/interactive_ui.py`, `src/ccbot/tmux_manager.py`

**Steps:**

- [ ] Run `/spec:work` on TASK-010
- [ ] Implement three recovery options for dead sessions
- [ ] Write tests
- [ ] Run `make check` - must pass

### Task 9: TASK-011 - /resume command

**Files:**

- Modify: `src/ccbot/bot.py`, `src/ccbot/session.py`

**Steps:**

- [ ] Run `/spec:work` on TASK-011
- [ ] Implement standalone resume command
- [ ] Write tests
- [ ] Run `make check` - must pass

---

## Phase E: UI Modernization (EPIC-004) - lower priority

### Task 10: TASK-012 - Topic emoji status updates

### Task 11: TASK-013 - Enhanced /sessions dashboard

### Task 12: TASK-014 - Status action buttons

**Steps:**

- [ ] Execute each via `/spec:work` in order
- [ ] Run `make check` after each - must pass

---

## Phase F: Fork Independence (EPIC-005 + EPIC-007) - lowest priority

### Tasks 13-18: TASK-016 through TASK-020, TASK-028 through TASK-031

Fork attribution, GitHub metadata, PyPI packaging, CI/CD, README, LICENSE.

**Steps:**

- [ ] Execute each via `/spec:work` in order
- [ ] Run `make check` after each - must pass

---

## Recommended Focus

Start with Phase A (5 minutes) + Phase B (the core ask about tmux sync), then continue with Phase C (already in-progress cleanup). Phases D-F are existing spec tasks to pick up via `/spec:work` as normal.

## Verification

- [ ] Manual test: create tmux window manually, verify topic auto-created in Telegram group
- [ ] Manual test: cold-start scenario with CCBOT_GROUP_ID set, no existing bindings
- [ ] Run full test suite: `make check`
- [ ] Verify all spec task statuses match actual implementation state

## Wrap-up

- [ ] Update README.md if user-facing changes
- [ ] Update CLAUDE.md if internal patterns changed
- [ ] Move this plan to `docs/plans/completed/`
