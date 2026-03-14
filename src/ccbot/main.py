"""Application entry point — CLI dispatcher and bot bootstrap.

Handles two execution modes:
  1. `ccbot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. `ccbot codex-map` — updates session_map.json for Codex rollout sessions.
  3. Default — configures logging, initializes tmux session, and starts the
     Telegram bot polling loop via bot.create_bot().
"""

import asyncio
import argparse
import logging
import os
import sys


def _parse_forward_ports(args: list[str]) -> list[int]:
    parser = argparse.ArgumentParser(
        prog="ccbot",
        description="Telegram monitor for AI CLI sessions",
    )
    parser.add_argument(
        "--forward",
        action="append",
        default=[],
        metavar="PORT[,PORT...]",
        help="Forward local port(s) to public URL and announce in Telegram",
    )
    parsed = parser.parse_args(args)

    ports: list[int] = []
    for group in parsed.forward:
        for token in group.split(","):
            part = token.strip()
            if not part:
                continue
            try:
                port = int(part)
            except ValueError as e:
                raise SystemExit(f"Invalid --forward value: {part}") from e
            if port < 1 or port > 65535:
                raise SystemExit(f"Invalid --forward port: {port}")
            ports.append(port)
    return ports


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main

        hook_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "codex-map":
        from .codex_mapper import codex_session_mapper

        changed = asyncio.run(codex_session_mapper.sync_session_map())
        print("updated" if changed else "no changes")
        return

    forward_ports = _parse_forward_ports(sys.argv[1:])
    if forward_ports:
        os.environ["CCBOT_FORWARD_PORTS"] = ",".join(str(p) for p in forward_ports)

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

    logging.getLogger("ccbot").setLevel(logging.DEBUG)
    # AIORateLimiter (max_retries=5) handles retries itself; keep INFO for visibility
    logging.getLogger("telegram.ext.AIORateLimiter").setLevel(logging.INFO)
    logger = logging.getLogger(__name__)

    from .tmux_manager import tmux_manager

    logger.info("Allowed users: %s", config.allowed_users)
    logger.info("Provider: %s", config.provider)
    logger.info("Provider data root: %s", config.provider_data_root)

    # Ensure tmux session exists
    session = tmux_manager.get_or_create_session()
    logger.info("Tmux session '%s' ready", session.session_name)

    logger.info("Starting Telegram bot...")
    from .bot import create_bot

    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
