"""Auth resource plugin for ResourceHub.

Auto-registers the token-provider config classes and their factories:
``keycloak:<name>`` and ``oauth2:<name>``. Both resolve to an object
with ``get_token()``, which is what an ``api_key: <category>:<name>``
reference on any other resource is resolved through.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.auth.config import KeycloakTokenConfig, OAuth2TokenConfig
from operonx.providers.auth.factory import create_auth, create_oauth2

_registered = False


def register():
    """Register the Keycloak and OAuth2 config classes and factories."""
    global _registered
    if _registered:
        return

    REGISTRY.register(KeycloakTokenConfig, create_auth)
    REGISTRY.register(OAuth2TokenConfig, create_oauth2)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
