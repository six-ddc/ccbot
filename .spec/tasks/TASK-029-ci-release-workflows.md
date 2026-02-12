---
id: TASK-029
title: Create CI and release workflows
status: done
epic: EPIC-007
---

# TASK-029: Create CI and release workflows

## Objective

Modern CI/CD with Python 3.13+3.14 matrix and automated PyPI release.

## Steps

1. Delete `.github/workflows/check.yml`
2. Create `.github/workflows/ci.yml`:
   - Trigger on push + PR to main
   - Matrix: Python 3.13, 3.14
   - Steps: checkout, setup-uv, sync, format check, lint, typecheck, test
3. Create `.github/workflows/release.yml`:
   - Trigger on tag push `v*`
   - Build with `uv build`
   - Publish via trusted publisher OIDC (pypa/gh-action-pypi-publish)

## Acceptance Criteria

- ci.yml covers format, lint, typecheck, test on 3.13+3.14
- release.yml uses trusted publisher (no API tokens)
- Workflow YAML is valid
