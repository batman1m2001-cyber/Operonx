"""Embedding resource plugin for ResourceHub.

Auto-registers embedding config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.embeddings.config import EmbeddingConfig
from hush.providers.embeddings.factory import EmbeddingFactory


class EmbeddingPlugin:
    """Plugin for auto-registering embedding resources with ResourceHub.

    Call EmbeddingPlugin.register() to register all embedding config classes and factory handlers.

    Example:
        ```python
        from hush.providers.registry import EmbeddingPlugin

        # Register once at startup
        EmbeddingPlugin.register()

        # Now ResourceHub can create embedding instances from configs
        from hush.core.registry import get_hub
        hub = get_hub()
        embedder = hub.embedding("bge-m3")
        ```
    """

    _registered = False

    @classmethod
    def register(cls):
        """Register embedding config class and factory handler."""
        if cls._registered:
            return

        # Register config class with factory
        REGISTRY.register(EmbeddingConfig, EmbeddingFactory.create)

        cls._registered = True

    @classmethod
    def is_registered(cls) -> bool:
        """Check if plugin has been registered."""
        return cls._registered


# Auto-register on import
EmbeddingPlugin.register()
