---
id: TASK-027
title: "Quality gates Stage B+C: complexity + naming rules"
status: todo
priority: normal
req: REQ-006
epic: EPIC-006
depends: [TASK-026]
---

# TASK-027: Quality gates Stage B+C

Enable remaining strict Ruff rules after refactoring is complete.

## Implementation Steps

1. **Stage B — Complexity rules** (for touched/refactored files):
   - `C901` — function complexity (max 10)
   - `PLR0911` — too many return statements
   - `PLR0912` — too many branches
   - `PLR0915` — too many statements
2. **Stage C — Naming rules** (all internal identifiers):
   - `N` — PEP 8 naming conventions
   - Fix any violations introduced during refactoring
3. Review and adjust thresholds if needed (document exceptions in pyproject.toml)
4. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- C901, PLR0911/12/15, and N rules enabled in Ruff config
- Zero violations across entire codebase
- Any per-file exceptions documented with `# noqa` + comment
- All tests pass
