"""Auth resource plugin for ResourceHub.

Auto-registers Keycloak config classes and factory handlers with operonx.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.auth.config import KeycloakTokenConfig
from operonx.providers.auth.factory import create_auth

_registered = False


def register():
    """Register Keycloak config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(KeycloakTokenConfig, create_auth)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
