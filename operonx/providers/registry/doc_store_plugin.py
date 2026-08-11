"""Document store resource plugin for ResourceHub.

Auto-registers doc store config classes and factory handlers with operonx.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.doc_stores.config import DocStoreConfig
from operonx.providers.doc_stores.factory import create_doc_store

_registered = False


def register():
    """Register doc store config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(DocStoreConfig, create_doc_store)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
