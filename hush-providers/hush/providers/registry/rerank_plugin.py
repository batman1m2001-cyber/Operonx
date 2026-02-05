"""Reranking resource plugin for ResourceHub.

Auto-registers reranking config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.rerankers.config import RerankingConfig
from hush.providers.rerankers.factory import RerankingFactory


class RerankPlugin:
    """Plugin for auto-registering reranking resources with ResourceHub.

    Call RerankPlugin.register() to register all reranking config classes and factory handlers.

    Example:
        ```python
        from hush.providers.registry import RerankPlugin

        # Register once at startup
        RerankPlugin.register()

        # Now ResourceHub can create reranker instances from configs
        from hush.core.registry import get_hub
        hub = get_hub()
        reranker = hub.reranker("bge-m3")
        ```
    """

    _registered = False

    @classmethod
    def register(cls):
        """Register reranking config class and factory handler."""
        if cls._registered:
            return

        # Register config class with factory
        REGISTRY.register(RerankingConfig, RerankingFactory.create)

        cls._registered = True

    @classmethod
    def is_registered(cls) -> bool:
        """Check if plugin has been registered."""
        return cls._registered


# Auto-register on import
RerankPlugin.register()
