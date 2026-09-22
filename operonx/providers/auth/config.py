"""Configuration for Keycloak token-based authentication."""

from typing import ClassVar, Optional

from operonx.core.utils import YamlModel


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


class OAuth2TokenConfig(YamlModel):
    """Configuration for OAuth2 ``client_credentials`` authentication.

    The sibling of :class:`KeycloakTokenConfig` for endpoints that speak
    plain OAuth2 rather than Keycloak's response shape — Databricks,
    Azure AD, and anything else issuing tokens from a
    ``grant_type=client_credentials`` POST with HTTP basic auth.

    Attributes:
        token_url: The OAuth2 token endpoint URL.
        client_id: OAuth2 client ID (also the HTTP basic username).
        client_secret: OAuth2 client secret (also the basic password).
        scope: OAuth2 scope, space-separated if multiple.
        token_path: JSON key holding the access token in the response.
        expires_in_path: Optional JSON key holding the lifetime in seconds.
            When absent, the token is assumed good for 59 minutes.
        refresh_interval: Background refresh interval in seconds.
        refresh_buffer: Seconds before expiry at which to refresh.
        verify_ssl: TLS verification on the token request. Defaults to
            **False** because these endpoints commonly sit behind a
            corporate TLS-inspecting proxy whose CA is not in the image's
            trust store. Set it to ``true`` wherever the chain does
            validate — the request carries the client secret, so an
            unverified connection is worth closing when you can.

    Example YAML::

        oauth2:databricks:
          token_url: https://workspace.cloud.databricks.com/oidc/v1/token
          client_id: ${DATABRICKS_CLIENT_ID}
          client_secret: ${DATABRICKS_CLIENT_SECRET}
          scope: all-apis
          refresh_interval: 1800
    """

    _category: ClassVar[str] = "oauth2"

    token_url: str
    client_id: str
    client_secret: str
    scope: str = "all-apis"
    token_path: str = "access_token"
    expires_in_path: Optional[str] = "expires_in"
    refresh_interval: float = 3600.0
    refresh_buffer: float = 300.0
    verify_ssl: bool = False
