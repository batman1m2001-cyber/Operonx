"""LLM resource plugin for ResourceHub.

Auto-registers LLM config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.llms.config import LLMConfig
from hush.providers.llms.factory import LLMFactory


class LLMPlugin:
    """Plugin for auto-registering LLM resources with ResourceHub.

    Call LLMPlugin.register() to register the LLM config class and factory handler.

    Example:
        ```python
        from hush.providers.registry import LLMPlugin

        # Register once at startup
        LLMPlugin.register()

        # Now ResourceHub can create LLM instances from configs
        from hush.core.registry import get_hub
        hub = get_hub()
        llm = hub.llm("gpt-4")
        ```
    """

    _registered = False

    @classmethod
    def register(cls):
        """Register LLM config class and factory handler."""
        if cls._registered:
            return

        REGISTRY.register(LLMConfig, LLMFactory.create)

        cls._registered = True

    @classmethod
    def is_registered(cls) -> bool:
        """Check if plugin has been registered."""
        return cls._registered


# Auto-register on import
LLMPlugin.register()
