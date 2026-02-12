---
id: TASK-016
title: Add fork attribution and rationale
status: done
req: REQ-005
epic: EPIC-005
depends: [TASK-015]
---

# TASK-016: Add fork attribution and rationale

Credit original author and explain fork rationale prominently.

## Implementation Steps

1. Add "Fork Information" section to README:
   - Link to original project
   - Credit original author
   - Explain rationale: "Maintained independently to [support multi-instance, faster iteration, custom features]"
   - Mention upstream remote is tracked
2. Update LICENSE if needed (if derived, maintain attribution)
3. Add FORK.md document explaining:
   - Timeline of fork (when created)
   - Key divergences from upstream
   - How to sync with upstream (if desired)
4. Keep upstream remote as `upstream` (or similar)

## Acceptance Criteria

- Original author credited in README
- Fork rationale clear and professional
- Upstream remote configured and documented
- No claims of original authorship
