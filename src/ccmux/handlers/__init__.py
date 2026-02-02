"""Telegram bot handlers package — modular handler organization.

This package contains the Telegram bot handlers split by functionality:
  - callback_data: Callback data constants (CB_* prefixes)
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers with MarkdownV2 fallback
  - history: Message history pagination
  - directory_browser: Directory selection UI
  - interactive_ui: Interactive UI (AskUserQuestion, Permission Prompt, etc.)
  - status_polling: Terminal status line polling
  - response_builder: Build paginated response messages
"""
