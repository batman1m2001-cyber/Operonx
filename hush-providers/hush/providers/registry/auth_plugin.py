"""Auth resource plugin for ResourceHub.

Auto-registers Keycloak config classes and factory handlers with hush-core.
"""

from hush.core.registry import REGISTRY
from hush.providers.auth.config import KeycloakTokenConfig
from hush.providers.auth.factory import AuthFactory


class AuthPlugin:
    """Plugin for auto-registering auth resources with ResourceHub.

    Call AuthPlugin.register() to register the Keycloak config class
    and factory handler.

    Example:
        from hush.providers.registry import AuthPlugin

        # Register once at startup
        AuthPlugin.register()

        # Now ResourceHub can create KeycloakTokenProvider instances
        from hush.core.registry import get_hub
        hub = get_hub()
        provider = hub.keycloak("myapp")
        token = provider.get_token()
    """

    _registered = False

    @classmethod
    def register(cls):
        """Register Keycloak config class and factory handler."""
        if cls._registered:
            return

        REGISTRY.register(KeycloakTokenConfig, AuthFactory.create)

        cls._registered = True

    @classmethod
    def is_registered(cls) -> bool:
        """Check if plugin has been registered."""
        return cls._registered


# Auto-register on import
AuthPlugin.register()
