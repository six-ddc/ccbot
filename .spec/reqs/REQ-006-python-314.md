---
id: REQ-006
title: Python 3.14 + Architecture Cleanup
version: 1
priority: high
---

# REQ-006: Python 3.14 + Architecture Cleanup

Upgrade ccbot to Python 3.14 and refactor the codebase for modularity, stricter quality gates, and maintainability.

## Success Criteria

1. **Runtime**: Python 3.14+ required, tooling (Ruff, Pyright, CI) targets 3.14
2. **Quality gates**: Stricter Ruff rules enforced in stages (ARG, G, BLE, SIM, C901, N)
3. **Modularity**: `bot.py` split into thin handlers + dispatch; `callback_handler` and `text_handler` decomposed
4. **Naming**: Short aliases (`wid`, `ws`, `w`) replaced with descriptive names in core flows
5. **State**: User-data keys centralized into constants or typed state objects
6. **Exceptions**: Broad `except Exception` narrowed; silent `pass` branches eliminated
7. **Logging**: Lazy formatting only (`logger.info("...", value)`), no f-strings in log calls
8. **Tests**: Characterization tests before each extraction; regression tests per extracted module
9. **All gates pass**: Every task ends with `make fmt && make test && make lint`

## Constraints

- One task per session — complete fully before starting the next
- No behavior changes — all existing tests pass, no API changes
- No merge without green checks
- Characterization tests added _before_ refactoring, not after
