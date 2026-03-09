"""LLM resource plugin for ResourceHub.

Auto-registers LLM config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.llms.config import LLMConfig
from hush.providers.llms.factory import create_llm

_registered = False


def register():
    """Register LLM config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(LLMConfig, create_llm)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
