"""CCBot - Telegram Bot for managing Claude Code sessions via tmux.

Package entry point. Exports the version string only; all functional
modules are imported lazily by main.py to keep startup fast.
"""

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"
