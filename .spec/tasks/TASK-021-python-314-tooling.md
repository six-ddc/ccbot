---
id: TASK-021
title: Upgrade runtime and tooling to Python 3.14
status: todo
priority: high
req: REQ-006
epic: EPIC-006
---

# TASK-021: Upgrade runtime and tooling to Python 3.14

Upgrade all project dependencies and tooling to target Python 3.14.

## Implementation Steps

1. Update `pyproject.toml`:
   - `requires-python = ">=3.14"`
   - Ruff target: `target-version = "py314"`
   - Pyright `pythonVersion = "3.14"`
2. Regenerate lockfile: `uv lock && uv sync`
3. Update docs (README.md) from 3.12+ to 3.14+
4. Add `make build` target (e.g. `uv build`) so all required gates exist
5. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- `requires-python = ">=3.14"` in pyproject.toml
- Ruff and Pyright target py314
- `make build` target exists and succeeds
- All existing tests pass on Python 3.14
- Lockfile regenerated
