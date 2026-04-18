"""Configuration for Keycloak token-based authentication."""

from typing import ClassVar, Optional

from operon.core.utils import YamlModel


class KeycloakTokenConfig(YamlModel):
    """Configuration for Keycloak token-based authentication.

    Used for fetching access tokens from a Keycloak/identity endpoint
    to authenticate with other services (e.g., LLM APIs).

    Attributes:
        url: The endpoint URL to fetch tokens from
        name: Client/application name for authentication
        secret: Client secret for authentication
        token_path: JSON path to extract the access token from response
        expires_in_path: Optional JSON path to extract token expiry (seconds)
        refresh_interval: Background refresh interval in seconds (default: 3600)
        refresh_buffer: Seconds before expiry to trigger refresh (default: 300)

    Example YAML:
        keycloak:myapp:
          url: https://identity.example.com/client/connect
          name: my_app
          secret: my_secret
          token_path: accessToken
          expires_in_path: expiresIn
          refresh_interval: 3600
    """

    _category: ClassVar[str] = "keycloak"

    url: str
    name: str
    secret: str
    token_path: str = "accessToken"
    expires_in_path: Optional[str] = "expiresIn"
    refresh_interval: float = 3600.0  # Background refresh every 1 hour
    refresh_buffer: float = 300.0  # Refresh 5 minutes before expiry
