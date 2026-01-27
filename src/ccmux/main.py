"""Entry point for CCMux."""

import logging

from .bot import create_bot
from .config import config
from .tmux_manager import tmux_manager


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )
    # Set our modules to DEBUG
    for name in ("ccmux",):
        logging.getLogger(name).setLevel(logging.DEBUG)
    logger = logging.getLogger(__name__)

    logger.info(f"Allowed users: {config.allowed_users}")
    logger.info(f"Claude projects path: {config.claude_projects_path}")

    # Ensure tmux session exists
    session = tmux_manager.get_or_create_session()
    logger.info(f"Tmux session '{session.session_name}' ready")

    logger.info("Starting Telegram bot...")
    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
