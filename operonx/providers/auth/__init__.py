"""Authentication providers for Operon.

Provides Keycloak token-based authentication with background refresh.
The `KeycloakTokenProvider` backend is lazy-loaded via PEP 562
``__getattr__`` so this package can be imported on tier-1 installs
(no ``httpx``). The missing-dep ImportError surfaces only when the
backend is actually accessed.
"""

from .config import KeycloakTokenConfig
from .factory import create_auth

_LAZY_BACKENDS = {
    "KeycloakTokenProvider": "operonx.providers.auth.keycloak",
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
]
