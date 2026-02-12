---
id: TASK-020
title: Cleanup and streamline CI/CD workflows
status: done
req: REQ-005
epic: EPIC-005
---

# TASK-020: Cleanup and streamline CI/CD workflows

Review and optimize GitHub Actions workflows.

## Implementation Steps

1. Review existing workflows in `.github/workflows/`:
   - List all workflows
   - Identify dead or obsolete ones
   - Check trigger conditions (on push, on PR, manual, scheduled)
2. Keep/refine:
   - Test: lint + typecheck + pytest on push/PR
   - Build/publish: on release tag
   - Any security scans
3. Remove:
   - Unused workflows
   - Dead code paths
4. Ensure:
   - Tests run on all PRs (required check)
   - Release process is automated
   - No hardcoded secrets (use GitHub Secrets)
5. Document workflow triggers in CONTRIBUTING.md

## Acceptance Criteria

- Only active, necessary workflows remain
- All tests pass on PR
- Release/publish process is clear
- No unused or dead workflows
