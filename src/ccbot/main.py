"""Application entry point — CLI dispatcher and bot bootstrap.

Handles two execution modes:
  1. `ccbot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. Default — configures logging, initializes tmux session, and starts the
     Telegram bot polling loop via bot.create_bot().
"""

import logging
import sys


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main

        hook_main()
        return

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    # Import config before enabling DEBUG — avoid leaking debug logs on config errors
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

    # Set up file logging with rotation (max 1MB, keep 3 backups)
    from logging.handlers import RotatingFileHandler

    log_file = config.config_dir / "ccbot.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    import os as _os
    if log_file.exists():
        _os.chmod(log_file, 0o600)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    logging.getLogger("ccbot").addHandler(file_handler)

    logging.getLogger("ccbot").setLevel(logging.DEBUG)
    # AIORateLimiter (max_retries=5) handles retries itself; keep INFO for visibility
    logging.getLogger("telegram.ext.AIORateLimiter").setLevel(logging.INFO)
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
    application.run_polling(allowed_updates=["message", "callback_query", "message_reaction"])


if __name__ == "__main__":
    main()
