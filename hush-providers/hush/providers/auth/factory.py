"""Factory for creating authentication providers."""

from .config import KeycloakTokenConfig
from .keycloak import KeycloakTokenProvider


class AuthFactory:
    """Factory for creating authentication providers."""

    @staticmethod
    def create(config: KeycloakTokenConfig) -> KeycloakTokenProvider:
        """Create a KeycloakTokenProvider from config.

        Args:
            config: KeycloakTokenConfig instance

        Returns:
            KeycloakTokenProvider instance
        """
        return KeycloakTokenProvider(config)
