"""Bot lifecycle orchestration and application setup.

Coordinates the Telegram bot application lifecycle, integrating all CCMux components.
Core responsibilities:
  - Application initialization: register handlers, start monitoring and polling
  - Bot command registration: /start, /list, /history, /screenshot, /esc, Claude Code commands
  - Lifecycle hooks: post_init (start monitors), post_shutdown (clean up resources)

Key functions: create_bot(), post_init(), post_shutdown()
"""

import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import config
from .handlers import (
    CC_COMMANDS,
    callback_handler,
    esc_command,
    forward_command_handler,
    history_command,
    list_command,
    screenshot_command,
    start_command,
    text_handler,
)
from .message_builder import handle_new_message
from .session_monitor import NewMessage, SessionMonitor
from .status_handler import start_status_polling, stop_status_polling

logger = logging.getLogger(__name__)

# Session monitor instance
session_monitor: SessionMonitor | None = None


async def post_init(application: Application) -> None:
    """Initialize bot: set commands, start session monitor and status polling."""
    global session_monitor

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("start", "Show session menu"),
        BotCommand("list", "List all sessions"),
        BotCommand("history", "Message history for active session"),
        BotCommand("screenshot", "Capture terminal screenshot"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
    ]
    # Add Claude Code slash commands
    for cmd_name, desc in CC_COMMANDS.items():
        bot_commands.append(BotCommand(cmd_name, desc))

    await application.bot.set_my_commands(bot_commands)

    monitor = SessionMonitor()

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot)

    monitor.set_message_callback(message_callback)
    monitor.start()
    session_monitor = monitor
    logger.info("Session monitor started")

    # Start status polling task
    await start_status_polling(application.bot)
    logger.info("Status polling task started")


async def post_shutdown(application: Application) -> None:
    """Shutdown bot: stop status polling, queue workers, and session monitor."""
    await stop_status_polling()

    # Import here to avoid circular dependency
    from .message_queue import cleanup_all_workers

    await cleanup_all_workers()

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")


def create_bot() -> Application:
    """Create and configure the Telegram bot application."""
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return application
