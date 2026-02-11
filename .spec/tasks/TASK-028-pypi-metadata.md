---
id: TASK-028
title: Configure hatch-vcs and PyPI metadata
status: todo
epic: EPIC-007
---

# TASK-028: Configure hatch-vcs and PyPI metadata

## Objective

Update `pyproject.toml` for proper PyPI distribution with version from git tags.

## Steps

1. Update `pyproject.toml`:
   - Replace static `version` with `dynamic = ["version"]`
   - Add `hatch-vcs` to build-system requires
   - Add `[tool.hatch.version]` source config
   - Add authors, license, classifiers, project URLs
2. Create `src/ccbot/_version.py` placeholder (hatch-vcs populates at build)
3. Verify `uv build` produces correct wheel

## Acceptance Criteria

- `uv build` succeeds
- Wheel filename contains version from git tag (or fallback)
- `pyproject.toml` has all required PyPI metadata
