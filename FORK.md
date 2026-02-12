# Fork Information

This project is a maintained fork of [six-ddc/ccbot](https://github.com/six-ddc/ccbot).

## Original Project

- **Author**: [six-ddc](https://github.com/six-ddc)
- **Repository**: https://github.com/six-ddc/ccbot
- **License**: MIT

## Fork Timeline

- **Original release**: February 7, 2026
- **Fork created**: February 8, 2026
- **Maintained by**: [Alexei Ledenev](https://github.com/alexei-led)

## Rationale

This fork is maintained independently to support:

- **Multi-instance deployment** — run multiple bot instances, each bound to a specific Telegram group
- **Topic-only architecture** — simplified routing with 1 topic = 1 tmux window = 1 Claude session
- **Interactive UI** — inline keyboards for AskUserQuestion, ExitPlanMode, and Permission prompts
- **Recovery flows** — Fresh/Continue/Resume options for dead sessions
- **Quality gates** — comprehensive test suite, type checking, and lint rules

## Key Divergences

- Complete rewrite of session routing (window ID-based instead of window name)
- Hook-based session tracking replacing manual session management
- Per-user message queue with FIFO ordering and message merging
- MarkdownV2 output with automatic fallback
- Python 3.14 requirement (up from 3.12)
- 350+ test suite with CI enforcement

## Syncing with Upstream

The upstream remote is configured as `upstream`:

```bash
# Fetch upstream changes
git fetch upstream

# Compare branches
git log --oneline upstream/main..HEAD

# Cherry-pick specific commits if needed
git cherry-pick <commit-hash>
```
