---
id: REQ-007
title: Fork Independence & PyPI Release
status: in_progress
epic: EPIC-007
---

# REQ-007: Fork Independence & PyPI Release

ccbot was forked from `six-ddc/ccmux` but has diverged significantly (42+ original commits, Python 3.14, full architecture rewrite). Time to declare independence with proper packaging and distribution.

## Success Criteria

1. Package builds and publishes to PyPI as `ccbot`
2. Version managed via git tags (`hatch-vcs`, semver)
3. CI runs on Python 3.13 + 3.14, includes typecheck step
4. Automated release workflow via GitHub Actions (trusted publisher OIDC)
5. README has badges, credits to original author, updated install instructions
6. LICENSE updated with fork copyright
7. CHANGELOG exists in Keep a Changelog format

## Constraints

- Package name: `ccbot` (available on PyPI)
- Initial version: `0.2.0` (via git tag `v0.2.0`)
- Keep `ccbot` as internal package name (already renamed throughout)
- Credit original author (six-ddc) in README and LICENSE
