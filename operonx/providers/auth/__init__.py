"""Authentication providers for Operon.

Provides Keycloak and OAuth2 client_credentials token providers, both
with background refresh and both exposing ``get_token()``.
Each backend is lazy-loaded via PEP 562
``__getattr__`` so this package can be imported on tier-1 installs
(no ``httpx``). The missing-dep ImportError surfaces only when the
backend is actually accessed.
"""

from .config import KeycloakTokenConfig, OAuth2TokenConfig
from .factory import create_auth, create_oauth2

_LAZY_BACKENDS = {
    "KeycloakTokenProvider": "operonx.providers.auth.keycloak",
    "OAuth2TokenProvider": "operonx.providers.auth.oauth2",
}


def __getattr__(name: str):
    if name in _LAZY_BACKENDS:
        import importlib

        module = importlib.import_module(_LAZY_BACKENDS[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "KeycloakTokenConfig",
    "KeycloakTokenProvider",
    "create_auth",
    "OAuth2TokenConfig",
    "OAuth2TokenProvider",
    "create_oauth2",
]
