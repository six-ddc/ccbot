---
id: TASK-022
title: "Quality gates Stage A: ARG, G, BLE, SIM, PLR2004"
status: todo
priority: normal
req: REQ-006
epic: EPIC-006
depends: [TASK-021]
---

# TASK-022: Quality gates Stage A

Enable stricter Ruff rules incrementally. Stage A focuses on low-friction, high-value rules.

## Implementation Steps

1. Enable Ruff rule groups in `pyproject.toml`:
   - `ARG` — unused function arguments
   - `G` — logging format strings (no f-strings in log calls)
   - `BLE` — blind exception catches
   - `SIM` — simplifiable code patterns
   - `PLR2004` — magic value comparisons
2. Fix all violations (expect mostly logging format + unused args)
3. Verify: `make fmt && make test && make lint`

## Acceptance Criteria

- All five rule groups enabled and zero violations
- No behavior changes (all tests pass)
- Logging calls use lazy formatting everywhere
