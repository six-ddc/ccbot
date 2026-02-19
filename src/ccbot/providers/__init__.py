"""Provider abstractions for multi-agent CLI backends.

Re-exports the protocol, event types, capability dataclass, registry, and
policy so consumers can do ``from ccbot.providers import registry, ...``.
"""

from ccbot.providers.base import (
    AgentMessage,
    AgentProvider,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers.claude import ClaudeProvider
from ccbot.providers.policy import CapabilityPolicy
from ccbot.providers.registry import ProviderRegistry, UnknownProviderError, registry

__all__ = [
    "AgentMessage",
    "AgentProvider",
    "CapabilityPolicy",
    "ClaudeProvider",
    "ProviderCapabilities",
    "ProviderRegistry",
    "SessionStartEvent",
    "StatusUpdate",
    "UnknownProviderError",
    "registry",
]
