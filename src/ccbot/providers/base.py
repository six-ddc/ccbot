"""Provider protocol and shared event types for multi-agent CLI backends.

Pure definitions only — no imports from existing ccbot modules to avoid
circular dependencies. Every agent provider (Claude, Codex, Gemini) must
satisfy the ``AgentProvider`` protocol.

Event types:
  - SessionStartEvent: emitted when a new session is detected
  - AgentMessage: a parsed message from the agent's transcript
  - StatusUpdate: a parsed terminal status line

Capability descriptor:
  - ProviderCapabilities: declares what features the provider supports
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

# ── Type aliases for AgentMessage fields ─────────────────────────────────
MessageRole = Literal["user", "assistant"]
ContentType = Literal["text", "thinking", "tool_use", "tool_result", "local_command"]

# ── Shared validation ────────────────────────────────────────────────────
# Alphanumeric + hyphens/underscores — rejects shell metacharacters.
RESUME_ID_RE = re.compile(r"^[\w-]+$")

# ── Sentinel constants for expandable quotes ─────────────────────────────
# Canonical source of truth — imported by transcript_parser.py and consumers.
EXPANDABLE_QUOTE_START = "\x02EXPQUOTE_START\x02"
EXPANDABLE_QUOTE_END = "\x02EXPQUOTE_END\x02"

# ── Event types ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SessionStartEvent:
    """Emitted when a provider session starts or is detected via hook."""

    session_id: str  # provider-specific session identifier (UUID for Claude)
    cwd: str  # absolute path to the project directory
    transcript_path: str  # path to the session's transcript file
    window_key: str  # tmux key, e.g. "ccbot:@0"


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """A single parsed message from the agent's transcript."""

    text: str
    role: MessageRole
    content_type: ContentType
    is_complete: bool = True
    tool_use_id: str | None = None
    tool_name: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """Parsed terminal status line from the agent's pane.

    Two modes depending on ``is_interactive``:
      - **Normal status** (is_interactive=False): ``raw_text`` is the text
        after the spinner, ``display_label`` is a short formatted label
        like "…reading".
      - **Interactive UI** (is_interactive=True): ``raw_text`` is the full
        interactive pane content, ``display_label`` is the UI type name
        (same as ``ui_type``), e.g. "AskUserQuestion".
    """

    raw_text: str
    display_label: str
    is_interactive: bool = False
    ui_type: str | None = None  # "AskUserQuestion", "ExitPlanMode", etc.


@dataclass(frozen=True, slots=True)
class DiscoveredCommand:
    """A command/skill discovered by a provider."""

    name: str  # Original name (e.g. "spec:work", "committing-code")
    description: str
    source: Literal["builtin", "skill", "command"]


# ── Capabilities ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Declares what features a provider supports.

    Immutable after construction — providers return a fixed instance.
    """

    name: str  # e.g. "claude", "codex", "gemini"
    launch_command: str  # e.g. "claude", "codex"
    supports_hook: bool = False
    supports_resume: bool = False
    supports_continue: bool = False
    supports_structured_transcript: bool = False
    transcript_format: Literal["jsonl", "plain"] = "jsonl"
    terminal_ui_patterns: tuple[str, ...] = ()
    builtin_commands: tuple[str, ...] = ()


# ── Provider protocol ────────────────────────────────────────────────────


@runtime_checkable
class AgentProvider(Protocol):
    """Protocol that every agent CLI provider must satisfy.

    Lifecycle: the active provider is resolved once by ``get_provider()``
    and cached in ``providers._active``. The registry creates a fresh
    instance per ``get()`` call. All methods are stateless — they receive
    input and return results without side effects.
    """

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        """Build CLI args string for launching the agent.

        Returns a string like ``--resume abc123`` or ``--continue``.
        Empty string for a fresh session.
        """
        ...

    def parse_hook_payload(self, payload: dict[str, Any]) -> SessionStartEvent | None:
        """Parse a hook's stdin JSON into a SessionStartEvent.

        Returns None if the payload is invalid or not from this provider.
        """
        ...

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        """Parse a single raw transcript line into a structured dict.

        Returns None for empty, invalid, or skipped lines.
        """
        ...

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        """Parse a batch of transcript entries into AgentMessages.

        Returns (messages, updated_pending_tools).
        """
        ...

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        """Parse captured pane text into a StatusUpdate.

        Returns None if no status line or interactive UI is detected.
        """
        ...

    def extract_bash_output(self, pane_text: str, command: str) -> str | None:
        """Extract ``!`` command output from a captured tmux pane.

        Returns the command echo and output lines, or None if not found.
        """
        ...

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool:
        """Return True if this entry represents a human turn in the conversation.

        Excludes tool results, summaries, system messages, and metadata.
        """
        ...

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None:
        """Parse a single raw transcript entry into an AgentMessage for history.

        Returns None for non-parseable entries (summaries, metadata, etc.).
        """
        ...

    def discover_commands(self, base_dir: str) -> list[DiscoveredCommand]:
        """Discover available commands/skills from the provider's config."""
        ...
