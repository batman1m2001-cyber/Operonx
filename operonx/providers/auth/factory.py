"""Factory function for creating authentication providers.

The Keycloak backend is imported lazily inside `create_auth()` so that
`from operonx.providers.auth.factory import create_auth` works on a
tier-1 install (no `httpx`). The missing-dep ImportError surfaces only
when a caller actually instantiates the auth provider.
"""

from .config import KeycloakTokenConfig, OAuth2TokenConfig


def create_auth(config: KeycloakTokenConfig) -> "KeycloakTokenProvider":  # noqa: F821
    """Create a KeycloakTokenProvider from config.

    Args:
        config: KeycloakTokenConfig instance.

    Returns:
        KeycloakTokenProvider instance.

    Raises:
        ImportError: with a pointer to the right `operonx[<extra>]`
            install if `httpx` (or another keycloak dep) is missing.
    """
    try:
        from .keycloak import KeycloakTokenProvider
    except ImportError as e:
        raise ImportError(
            "KeycloakTokenProvider requires additional packages.\n"
            "  Install with: pip install operonx[providers]\n"
            f"  Original error: {e}"
        ) from e
    return KeycloakTokenProvider(config)


def create_oauth2(config: "OAuth2TokenConfig") -> "OAuth2TokenProvider":  # noqa: F821
    """Create an OAuth2TokenProvider from config.

    Args:
        config: OAuth2TokenConfig instance.

    Returns:
        OAuth2TokenProvider instance.

    Raises:
        ImportError: with a pointer to the right `operonx[<extra>]`
            install if `httpx` is missing.
    """
    try:
        from .oauth2 import OAuth2TokenProvider
    except ImportError as e:
        raise ImportError(
            "OAuth2TokenProvider requires additional packages.\n"
            "  Install with: pip install operonx[providers]\n"
            f"  Original error: {e}"
        ) from e
    return OAuth2TokenProvider(config)
