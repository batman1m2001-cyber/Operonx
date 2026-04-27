"""Reranking resource plugin for ResourceHub.

Auto-registers reranking config classes and factory handlers with operonx.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.rerankers.config import RerankingConfig
from operonx.providers.rerankers.factory import create_reranking

_registered = False


def register():
    """Register reranking config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(RerankingConfig, create_reranking)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
