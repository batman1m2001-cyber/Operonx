"""Vector store resource plugin for ResourceHub.

Auto-registers vector store config classes and factory handlers with operonx.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.vector_stores.config import VectorStoreConfig
from operonx.providers.vector_stores.factory import create_vector_store

_registered = False


def register():
    """Register vector store config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(VectorStoreConfig, create_vector_store)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
