"""Factory function for creating authentication providers.

The Keycloak backend is imported lazily inside `create_auth()` so that
`from operonx.providers.auth.factory import create_auth` works on a
tier-1 install (no `httpx`). The missing-dep ImportError surfaces only
when a caller actually instantiates the auth provider.
"""

from .config import KeycloakTokenConfig


def create_auth(config: KeycloakTokenConfig):
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
