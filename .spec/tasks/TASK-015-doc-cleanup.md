---
id: TASK-015
title: Clean up documentation structure
status: done
req: REQ-005
epic: EPIC-005
---

# TASK-015: Clean up documentation structure

Reorganize docs into a lean, hierarchical structure. Remove duplicates and video files.

## Implementation Steps

1. Review current docs:
   - README.md (main entry point)
   - README_CN.md (duplicate — consider removing or archiving)
   - docs/ folder structure
   - Any duplicate content
2. Remove/archive:
   - Chinese video files
   - Duplicate sections
   - Outdated content
3. Create new structure:
   - `/README.md` — Concise intro + quick start
   - `/docs/GETTING_STARTED.md` — Installation, basic setup, first session
   - `/docs/GUIDES.md` — How-to guides (multi-instance, deployment, customization)
   - `/docs/ARCHITECTURE.md` — System design (may reference `.claude/rules/`)
   - `/docs/CONTRIBUTING.md` — Development setup
4. Ensure cross-references are consistent
5. Add table of contents to README

## Acceptance Criteria

- README is under 500 lines (excluding TOC)
- No duplicate sections
- Clear progression: Quick Start → Guides → Deep Dive
- All Chinese video files removed
- Internal links verified and updated
