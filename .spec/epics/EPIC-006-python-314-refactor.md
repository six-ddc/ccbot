---
id: EPIC-006
title: "Python 3.14 + Architecture Cleanup"
reqs: [REQ-006]
tasks: [TASK-021, TASK-022, TASK-023, TASK-024, TASK-025, TASK-026, TASK-027]
---

# EPIC-006: Python 3.14 + Architecture Cleanup

Upgrade runtime to Python 3.14, establish stricter quality gates, refactor monolithic handlers into focused modules, normalize naming/state, and harden exception handling.

## Tasks (in order)

1. TASK-021: Upgrade runtime and tooling to Python 3.14
2. TASK-022: Quality gates Stage A (ARG, G, BLE, SIM, PLR2004)
3. TASK-023: Refactor callback_handler into dispatch map + modules
4. TASK-024: Refactor text_handler into explicit steps
5. TASK-025: Normalize naming and centralize state handling
6. TASK-026: Harden exception strategy and logging
7. TASK-027: Quality gates Stage B+C (complexity + naming rules)
