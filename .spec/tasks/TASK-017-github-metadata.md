---
id: TASK-017
title: Update GitHub project metadata
status: done
req: REQ-005
epic: EPIC-005
depends: [TASK-016]
---

# TASK-017: Update GitHub project metadata

Ensure GitHub repo description, labels, and project settings reflect the standalone fork.

## Implementation Steps

1. Update repo description (Settings):
   - Current: [check what's there]
   - Proposed: "Telegram bot bridging Telegram topics to Claude Code sessions via tmux (maintained fork)"
2. Review and update topics/tags:
   - Add: telegram-bot, claude, tmux, ai-assistant
   - Remove: any obsolete ones
3. Check labels:
   - Ensure they exist: bug, feature-request, help-wanted, documentation, etc.
   - Remove unused labels
4. Verify visibility settings (public, open to contributions)
5. Update repo sidebar (About section):
   - Website: none or link to docs
   - Description: match the one above

## Acceptance Criteria

- Repo description is concise and accurate
- Labels are relevant and used consistently
- Project is open to contributions (if desired)
- GitHub "About" section reflects current purpose
