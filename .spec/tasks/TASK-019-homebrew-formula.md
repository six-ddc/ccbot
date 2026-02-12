---
id: TASK-019
title: Create Homebrew formula
status: done
req: REQ-005
epic: EPIC-005
depends: [TASK-018]
---

# TASK-019: Create Homebrew formula

Add Homebrew support for easy macOS installation.

## Implementation Steps

1. Create Homebrew formula file:
   - Location: `Formula/ccbot.rb` (in personal tap or community-tap)
   - Reference PyPI package from TASK-018
   - Specify dependencies (python-telegram-bot, libtmux, etc.)
2. Test locally:
   - `brew install ./Formula/ccbot.rb`
   - Verify `ccbot --help` works
3. Publish to Homebrew (either):
   - Personal tap: `alexei/homebrew-ccbot`
   - Community tap (if popularity warrants)
4. Add installation instructions to README

## Acceptance Criteria

- `brew install` works (from personal or community tap)
- ccbot CLI accessible after installation
- Dependencies installed correctly
- Formula metadata is accurate
