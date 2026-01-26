"""Configuration management for CCMux."""

import os
from pathlib import Path

from dotenv import load_dotenv


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:
        load_dotenv()

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

        allowed_users_str = os.getenv("ALLOWED_USERS", "")
        if not allowed_users_str:
            raise ValueError("ALLOWED_USERS environment variable is required")
        self.allowed_users: set[int] = {
            int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()
        }

        # Tmux session name
        self.tmux_session_name = os.getenv("TMUX_SESSION_NAME", "ccmux")

        # State file for persisting user subscriptions
        self.state_file = Path.home() / ".ccmux" / "state.json"

        # Claude Code session monitoring configuration
        self.claude_projects_path = Path.home() / ".claude" / "projects"
        self.monitor_poll_interval = float(os.getenv("MONITOR_POLL_INTERVAL", "2.0"))
        self.monitor_state_file = Path.home() / ".ccmux" / "monitor_state.json"
        self.monitor_stable_wait = float(os.getenv("MONITOR_STABLE_WAIT", "2.0"))

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a user is in the allowed list."""
        return user_id in self.allowed_users


config = Config()
