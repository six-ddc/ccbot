"""Discover Claude Code commands for Telegram bot menu registration.

Scans three sources to build the command list:
  1. Built-in CC commands (always present)
  2. User-invocable skills from ~/.claude/skills/
  3. Custom commands from ~/.claude/commands/

Core components:
  - CCCommand dataclass: name, telegram_name, description, source
  - discover_cc_commands(): filesystem scanner with caching
  - register_commands(): sets Telegram bot menu (BotCommand list)
  - get_cc_name(): reverse lookup from sanitized telegram name to CC name
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ccbot.providers.base import AgentProvider
from telegram import Bot, BotCommand

logger = logging.getLogger(__name__)

# Built-in Claude Code commands (always registered)
CC_BUILTINS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
    "memory": "↗ Edit CLAUDE.md",
    "model": "↗ Select model and thinking effort",
}

# Bot-native commands (registered first, not from CC)
_BOT_COMMANDS: list[tuple[str, str]] = [
    ("new", "Create new Claude session"),
    ("history", "Message history for this topic"),
    ("sessions", "Sessions dashboard"),
    ("resume", "Browse and resume past sessions"),
    ("upgrade", "Upgrade ccbot and restart"),
]

# Telegram limits: max 100 commands, descriptions max 256 chars
_MAX_TELEGRAM_COMMANDS = 100
_MAX_DESCRIPTION_LEN = 256

_FrontmatterReadError = (OSError, UnicodeDecodeError)


@dataclass(frozen=True, slots=True)
class CCCommand:
    """A discovered Claude Code command."""

    name: str  # Original CC name (e.g. "spec:work", "committing-code")
    telegram_name: str  # Sanitized for Telegram (e.g. "spec_work")
    description: str
    source: Literal["builtin", "skill", "command"]


def _sanitize_telegram_name(name: str) -> str:
    """Sanitize a CC command name for Telegram.

    Telegram allows only [a-z0-9_] in command names, max 32 chars.
    Returns empty string for unrepresentable names.
    """
    sanitized = name.lower().replace("-", "_").replace(":", "_")
    # Strip anything not alphanumeric or underscore
    sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
    return sanitized[:32]


def _cc_desc(desc: str) -> str:
    """Ensure description has ↗ prefix for CC-forwarded commands."""
    return desc if desc.startswith("↗") else f"↗ {desc}"


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a markdown file.

    Simple key:value parser — no PyYAML dependency. Handles the subset
    needed for skills: name, description, user-invocable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except _FrontmatterReadError:
        return {}

    if not text.startswith("---"):
        return {}

    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}

    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip().strip("\"'")
    return result


def _safe_iterdir(path: Path) -> list[Path]:
    """Sorted directory listing, returning empty list on permission errors."""
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def discover_cc_commands(claude_dir: Path | None = None) -> list[CCCommand]:
    """Scan filesystem for CC commands.

    Sources (in order):
      1. Built-in commands (CC_BUILTINS)
      2. Skills: {claude_dir}/skills/*/SKILL.md (user-invocable only)
      3. Custom commands: {claude_dir}/commands/{group}/*.md

    Commands with empty sanitized names are skipped.
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"

    commands: list[CCCommand] = []

    # 1. Builtins
    for name, desc in CC_BUILTINS.items():
        commands.append(
            CCCommand(
                name=name,
                telegram_name=_sanitize_telegram_name(name),
                description=desc,
                source="builtin",
            )
        )

    # 2. Skills
    skills_dir = claude_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in _safe_iterdir(skills_dir):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            fm = parse_frontmatter(skill_file)
            if fm.get("user-invocable", "").lower() != "true":
                continue
            name = fm.get("name", skill_dir.name)
            tg_name = _sanitize_telegram_name(name)
            if not tg_name:
                continue
            desc = fm.get("description", f"↗ /{name}")
            commands.append(
                CCCommand(
                    name=name,
                    telegram_name=tg_name,
                    description=_cc_desc(desc),
                    source="skill",
                )
            )

    # 3. Custom commands
    commands_dir = claude_dir / "commands"
    if commands_dir.is_dir():
        for group_dir in _safe_iterdir(commands_dir):
            if not group_dir.is_dir() or group_dir.name.startswith("."):
                continue
            try:
                md_files = sorted(group_dir.glob("*.md"))
            except OSError:
                continue
            for cmd_file in md_files:
                if cmd_file.name.startswith("."):
                    continue
                name = f"{group_dir.name}:{cmd_file.stem}"
                tg_name = _sanitize_telegram_name(name)
                if not tg_name:
                    continue
                fm = parse_frontmatter(cmd_file)
                desc = fm.get("description", f"↗ /{name}")
                commands.append(
                    CCCommand(
                        name=name,
                        telegram_name=tg_name,
                        description=_cc_desc(desc),
                        source="command",
                    )
                )

    return commands


# Module-level cache (telegram_name → cc_name, first-wins to match registration)
_name_map: dict[str, str] = {}


def _refresh_cache(
    claude_dir: Path | None = None,
    provider: AgentProvider | None = None,
) -> list[CCCommand]:
    """Re-discover commands and update the cache.

    When *provider* is given, uses ``provider.discover_commands()`` which
    returns ``list[DiscoveredCommand]``.
    Falls back to filesystem scanning via ``discover_cc_commands()`` otherwise.
    """
    global _name_map
    if provider is not None:
        base_dir = str(claude_dir) if claude_dir else str(Path.home() / ".claude")
        valid_sources = {"builtin", "skill", "command"}
        discovered = provider.discover_commands(base_dir)
        commands = [
            CCCommand(
                name=cmd.name,
                telegram_name=_sanitize_telegram_name(cmd.name),
                description=_cc_desc(cmd.description),
                source=cast(
                    Literal["builtin", "skill", "command"],
                    cmd.source if cmd.source in valid_sources else "command",
                ),
            )
            for cmd in discovered
            if cmd.name
        ]
    else:
        commands = discover_cc_commands(claude_dir)
    # First-wins: matches the dedup order in register_commands
    new_map: dict[str, str] = {}
    for cmd in commands:
        if cmd.telegram_name not in new_map:
            new_map[cmd.telegram_name] = cmd.name
    _name_map = new_map
    return commands


def get_cc_name(telegram_name: str) -> str | None:
    """Look up the original CC command name from a sanitized Telegram name."""
    return _name_map.get(telegram_name)


async def register_commands(
    bot: Bot,
    claude_dir: Path | None = None,
    provider: AgentProvider | None = None,
) -> None:
    """Discover CC commands and register them in the Telegram bot menu.

    When *provider* is given, command discovery is delegated to the provider.
    Registers bot-native commands first (new, history, etc.), then up to
    the remaining Telegram limit of discovered CC commands. Deduplicates
    by telegram_name (first-wins) and excludes collisions with bot-native names.
    """
    commands = _refresh_cache(claude_dir, provider=provider)

    bot_commands = [BotCommand(name, desc) for name, desc in _BOT_COMMANDS]
    max_cc = _MAX_TELEGRAM_COMMANDS - len(bot_commands)

    # Pre-populate with bot-native names to avoid collisions
    seen_names: set[str] = {name for name, _ in _BOT_COMMANDS}
    cc_count = 0
    for cmd in commands:
        if cc_count >= max_cc:
            break
        # Skip empty names, duplicates, and bot-native collisions
        if not cmd.telegram_name or cmd.telegram_name in seen_names:
            continue
        seen_names.add(cmd.telegram_name)
        desc = cmd.description[:_MAX_DESCRIPTION_LEN]
        bot_commands.append(BotCommand(cmd.telegram_name, desc))
        cc_count += 1

    await bot.delete_my_commands()
    await bot.set_my_commands(bot_commands)
    logger.info("Registered %d bot commands (%d CC)", len(bot_commands), cc_count)
