"""Application entry point — CLI dispatcher and bot bootstrap.

Handles two execution modes:
  1. `ccbot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. Default — configures logging (respects CCBOT_LOG_LEVEL env var),
     initializes tmux session, and starts the Telegram bot polling loop
     via bot.create_bot().
"""

import logging
import os
import sys


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main

        hook_main()
        return

    log_level = os.environ.get("CCBOT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.WARNING,
    )

    # Import config before enabling ccbot loggers — avoid leaking debug logs on config errors
    try:
        from .config import config
    except ValueError as e:
        from .utils import ccbot_dir

        config_dir = ccbot_dir()
        env_path = config_dir / ".env"
        print(f"Error: {e}\n")
        print(f"Create {env_path} with the following content:\n")
        print("  TELEGRAM_BOT_TOKEN=your_bot_token_here")
        print("  ALLOWED_USERS=your_telegram_user_id")
        print()
        print("Get your bot token from @BotFather on Telegram.")
        print("Get your user ID from @userinfobot on Telegram.")
        sys.exit(1)

    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        logging.warning("Invalid CCBOT_LOG_LEVEL=%r, defaulting to INFO", log_level)
        numeric_level = logging.INFO
    logging.getLogger("ccbot").setLevel(numeric_level)
    # Silence chatty third-party loggers
    for name in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)

    from .tmux_manager import tmux_manager

    logger.info("Allowed users: %s", config.allowed_users)
    logger.info("Claude projects path: %s", config.claude_projects_path)

    # Ensure tmux session exists
    session = tmux_manager.get_or_create_session()
    logger.info("Tmux session '%s' ready", session.session_name)

    logger.info("Starting Telegram bot...")
    from .bot import create_bot

    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
