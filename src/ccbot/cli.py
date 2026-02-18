"""Click-based CLI for ccbot.

Defines the top-level command group and the ``run`` subcommand with all
bot-configuration flags.  Precedence: CLI flag > env var > .env > default.
``apply_args_to_env()`` sets os.environ for explicitly provided flags so
Config reads the overridden values.
"""

import os
from pathlib import Path

import click

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _validate_positive_float(
    _ctx: click.Context, _param: click.Parameter, value: float | None
) -> float | None:
    if value is not None and value <= 0:
        raise click.BadParameter("must be positive")
    return value


def _validate_non_negative_int(
    _ctx: click.Context, _param: click.Parameter, value: int | None
) -> int | None:
    if value is not None and value < 0:
        raise click.BadParameter("must be non-negative")
    return value


class _DefaultToRun(click.Group):
    """Click group that runs the ``run`` command when invoked without a subcommand."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If the first arg is not a known command and not --help/--version,
        # prepend "run" so flags like -v go to the run command.
        if args and args[0] not in self.commands and not args[0].startswith("--"):
            args = ["run", *args]
        return super().parse_args(ctx, args)


@click.group(
    cls=_DefaultToRun,
    invoke_without_command=True,
    help="Telegram bot bridging Telegram topics to Claude Code sessions via tmux.",
)
@click.version_option(package_name="ccbot", prog_name="ccbot")
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_cmd)


# --- run command -----------------------------------------------------------

# Mapping: click option name → environment variable name
_FLAG_TO_ENV: list[tuple[str, str]] = [
    ("config_dir", "CCBOT_DIR"),
    ("allowed_users", "ALLOWED_USERS"),
    ("tmux_session", "TMUX_SESSION_NAME"),
    ("claude_command", "CLAUDE_COMMAND"),
    ("monitor_interval", "MONITOR_POLL_INTERVAL"),
    ("group_id", "CCBOT_GROUP_ID"),
    ("instance_name", "CCBOT_INSTANCE_NAME"),
    ("autoclose_done", "AUTOCLOSE_DONE_MINUTES"),
    ("autoclose_dead", "AUTOCLOSE_DEAD_MINUTES"),
]


def apply_args_to_env(**kwargs: object) -> None:
    """Set environment variables from explicitly provided CLI flags.

    Call BEFORE Config instantiation to ensure CLI flags take precedence.
    Only sets env vars for flags that were explicitly provided (not None).
    """
    verbose = kwargs.get("verbose", False)
    log_level = kwargs.get("log_level")

    if verbose:
        os.environ["CCBOT_LOG_LEVEL"] = "DEBUG"
    elif log_level is not None:
        os.environ["CCBOT_LOG_LEVEL"] = str(log_level).upper()

    for attr, env_var in _FLAG_TO_ENV:
        value = kwargs.get(attr)
        if value is None:
            continue
        if isinstance(value, Path):
            os.environ[env_var] = str(value.expanduser().resolve())
        else:
            os.environ[env_var] = str(value)


@cli.command("run")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.option(
    "--log-level",
    type=click.Choice(_LOG_LEVELS, case_sensitive=False),
    default=None,
    help="Logging level.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=None,
    envvar="CCBOT_DIR",
    help="Config directory (default: ~/.ccbot).",
)
@click.option(
    "--allowed-users",
    default=None,
    envvar="ALLOWED_USERS",
    help="Comma-separated Telegram user IDs.",
)
@click.option(
    "--tmux-session",
    default=None,
    envvar="TMUX_SESSION_NAME",
    help="Tmux session name (default: ccbot).",
)
@click.option(
    "--claude-command",
    default=None,
    envvar="CLAUDE_COMMAND",
    help="Claude command (default: claude).",
)
@click.option(
    "--monitor-interval",
    type=float,
    default=None,
    callback=_validate_positive_float,
    envvar="MONITOR_POLL_INTERVAL",
    help="Poll interval in seconds (default: 2.0).",
)
@click.option(
    "--group-id",
    type=int,
    default=None,
    envvar="CCBOT_GROUP_ID",
    help="Restrict to one Telegram group.",
)
@click.option(
    "--instance-name",
    default=None,
    envvar="CCBOT_INSTANCE_NAME",
    help="Display label for multi-instance.",
)
@click.option(
    "--autoclose-done",
    type=int,
    default=None,
    callback=_validate_non_negative_int,
    envvar="AUTOCLOSE_DONE_MINUTES",
    help="Auto-close done topics after N minutes (default: 30, 0=disabled).",
)
@click.option(
    "--autoclose-dead",
    type=int,
    default=None,
    callback=_validate_non_negative_int,
    envvar="AUTOCLOSE_DEAD_MINUTES",
    help="Auto-close dead sessions after N minutes (default: 10, 0=disabled).",
)
def run_cmd(**kwargs: object) -> None:
    """Start the bot with optional overrides."""
    apply_args_to_env(**kwargs)

    from .main import run_bot

    run_bot()


# --- hook command ----------------------------------------------------------


@cli.command("hook")
@click.option(
    "--install", is_flag=True, help="Install hook into ~/.claude/settings.json."
)
@click.option(
    "--uninstall", is_flag=True, help="Remove hook from ~/.claude/settings.json."
)
@click.option("--status", is_flag=True, help="Check if hook is installed.")
def hook_cmd(install: bool, uninstall: bool, status: bool) -> None:
    """Claude Code session tracking hook."""
    from .hook import hook_main

    hook_main(install=install, uninstall=uninstall, status=status)


# --- status command --------------------------------------------------------


@cli.command("status")
def status_cmd() -> None:
    """Show running state."""
    from .status_cmd import status_main

    status_main()


# --- doctor command --------------------------------------------------------


@cli.command("doctor")
@click.option("--fix", is_flag=True, help="Auto-fix issues where possible.")
def doctor_cmd(fix: bool) -> None:
    """Validate setup and diagnose issues."""
    from .doctor_cmd import doctor_main

    doctor_main(fix=fix)
