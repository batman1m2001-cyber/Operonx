"""Factory function for creating authentication providers."""

from .config import KeycloakTokenConfig
from .keycloak import KeycloakTokenProvider


def create_auth(config: KeycloakTokenConfig) -> KeycloakTokenProvider:
    """Create a KeycloakTokenProvider from config.

    Args:
        config: KeycloakTokenConfig instance.

    Returns:
        KeycloakTokenProvider instance.
    """
    return KeycloakTokenProvider(config)
