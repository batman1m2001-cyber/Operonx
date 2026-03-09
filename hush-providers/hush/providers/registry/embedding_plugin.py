"""Embedding resource plugin for ResourceHub.

Auto-registers embedding config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.embeddings.config import EmbeddingConfig
from hush.providers.embeddings.factory import create_embedding

_registered = False


def register():
    """Register embedding config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(EmbeddingConfig, create_embedding)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
