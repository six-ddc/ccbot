"""Application entry point — Click CLI dispatcher and bot bootstrap.

The ``main()`` function invokes the Click command group defined in cli.py,
which dispatches to subcommands (run, hook, status, doctor).
``run_bot()`` contains the actual bot startup logic, called by the ``run``
command after CLI flags have been applied to the environment.
"""

import logging
import os
import sys


class _ShortNameFilter(logging.Filter):
    """Strip 'ccbot.' and 'handlers.' prefixes, cap at 20 chars."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name.startswith("ccbot.handlers."):
            name = name[len("ccbot.handlers.") :]
        elif name.startswith("ccbot."):
            name = name[len("ccbot.") :]
        record.short_name = name[:20]  # type: ignore[attr-defined]
        return True


def setup_logging(log_level: str) -> None:
    """Configure colored, compact logging for interactive CLI use."""
    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    try:
        import colorlog

        handler = colorlog.StreamHandler()
        handler.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s%(asctime)s %(levelname)-8s %(short_name)-20s %(message)s",
                datefmt="%H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    except ImportError:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(short_name)-20s %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    handler.addFilter(_ShortNameFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    logging.getLogger("ccbot").setLevel(numeric_level)
    for name in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)


def run_bot() -> None:
    """Start the bot. Called by the ``run`` Click command after env is set."""
    log_level = os.environ.get("CCBOT_LOG_LEVEL", "INFO").upper()
    setup_logging(log_level)

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

    logger = logging.getLogger(__name__)

    from .tmux_manager import tmux_manager

    logger.info("Allowed users: %s", config.allowed_users)
    logger.info("Claude projects path: %s", config.claude_projects_path)

    session = tmux_manager.get_or_create_session()
    logger.info("Tmux session '%s' ready", session.session_name)

    logger.info("Starting Telegram bot...")
    from .bot import create_bot

    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


def main() -> None:
    """Main entry point — dispatches via Click CLI group."""
    from .cli import cli

    cli()


if __name__ == "__main__":
    main()
