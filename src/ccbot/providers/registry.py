"""Provider registry — maps provider names to classes, instantiates on demand.

The module-level ``registry`` singleton starts empty; providers are
registered lazily via ``_ensure_registered()`` in ``ccbot.providers.__init__``
before first use. ``get()`` creates a **new instance** on each call — use
``get_provider()`` from ``ccbot.providers`` for cached singleton access.
"""

import logging

from ccbot.providers.base import AgentProvider

logger = logging.getLogger(__name__)


class UnknownProviderError(LookupError):
    """Raised when requesting a provider name that is not registered."""


class ProviderRegistry:
    """Maps provider name strings to AgentProvider classes.

    Note: ``get()`` creates a **new instance** on each call. Use
    ``get_provider()`` from ``ccbot.providers`` for cached singleton access.
    """

    def __init__(self) -> None:
        self._providers: dict[str, type[AgentProvider]] = {}

    def register(self, name: str, provider_cls: type[AgentProvider]) -> None:
        """Register a provider class under *name* (overwrites silently)."""
        self._providers[name] = provider_cls
        logger.debug("Registered provider %r", name)

    def get(self, name: str) -> AgentProvider:
        """Instantiate and return the provider registered under *name*.

        Raises ``UnknownProviderError`` if *name* is not registered.
        """
        cls = self._providers.get(name)
        if cls is None:
            available = ", ".join(sorted(self._providers)) or "(none)"
            raise UnknownProviderError(
                f"Unknown provider {name!r}. Available: {available}"
            )
        return cls()


registry = ProviderRegistry()
