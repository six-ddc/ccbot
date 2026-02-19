---
id: TASK-035
title: Provider registry and centralized capability policy
status: done
priority: high
req: REQ-008
epic: EPIC-008
depends:
  - TASK-034
---

# TASK-035: Provider registry and centralized capability policy

Implement provider loading/selection and a single capability policy layer so
feature gating does not leak into core handlers.

## Implementation Steps

1. Implement provider registry for configured provider selection per instance.
2. Add centralized capability policy used by core modules for feature availability.
3. Introduce generic provider config keys (provider id + launch command override).
4. Add tests for provider selection, invalid provider handling, and capability evaluation.
5. Verify: `make fmt && make test && make lint`.

## Acceptance Criteria

- Active provider is selected via config without direct provider checks in handlers.
- Capability decisions are centralized and test-covered.
- Config surface supports current Claude default and future providers.
- All quality gates pass.
