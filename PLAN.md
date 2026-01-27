# Bot Enhancement Plan

## 1. Save research doc to `doc/telegram-bot-features.md`

## 2. Add Claude Code slash commands as Telegram bot commands
No-parameter commands to add:
- `/clear` - Clear conversation history
- `/compact` - Compact conversation context
- `/cost` - Show token/cost usage
- `/help` - Show Claude Code help
- `/review` - Code review
- `/status` - Show status menu (existing, enhanced)
- `/list` - List sessions
- `/history` - Show message history for active session

These will send the corresponding slash command text to the active tmux window.

## 3. Bot interaction improvements (in bot.py)
- **send_chat_action("typing")** before sending placeholder
- **HTML parse_mode** for Claude responses with code block highlighting
- **reply_to_message_id** to link bot responses to user messages
- **Inline action buttons** on session detail (History / Refresh / Kill)
- **callback_query.answer(text)** with feedback text everywhere
- **Spoiler for thinking** content (`<tg-spoiler>`)
- **PendingResponse** stores original user message_id for reply linking

## 4. Files to modify
- `src/ccmux/bot.py` - Main changes
- `src/ccmux/telegram_sender.py` - Add markdown→HTML converter
- New: `doc/telegram-bot-features.md`
