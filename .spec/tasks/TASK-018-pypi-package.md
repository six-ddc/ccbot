---
id: TASK-018
title: Publish Python package (PyPI)
status: done
req: REQ-005
epic: EPIC-005
---

# TASK-018: Publish Python package (PyPI)

Package ccbot for distribution via PyPI (Python Package Index).

## Implementation Steps

1. Review `pyproject.toml`:
   - Ensure `name`, `version`, `description` are correct
   - Add `authors`, `license`, `keywords`
   - Set homepage, repository URLs
2. Create build backend (if not present):
   - Ensure `build-backend = "hatchling.build"` or similar
3. Build and test locally:
   - `uv build` or `python -m build`
   - Verify wheel and sdist
4. Create PyPI account (if not present)
5. Upload:
   - `uv publish` or `twine upload`
6. Test installation:
   - `pip install ccbot` in fresh venv
   - Verify CLI works: `ccbot --help`
7. Add installation instructions to README

## Acceptance Criteria

- `pip install ccbot` works
- CLI entrypoint (`ccbot`) is accessible
- Package metadata (author, license) is complete
- PyPI page is live and accurate
