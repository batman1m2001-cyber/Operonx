"""Transport lookup: a registered name, or an import path to your own.

`kind` in a `[[serve]]` block resolves through here. Built-ins register
under short names because most projects want a WebSocket and nobody should
write a handshake twice; a project's own transport registers the same way,
or is named outright as ``module:Class`` and needs no registration at all.

There is no privileged set. If a built-in can do something a third-party
transport cannot, the interface is wrong.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, Optional

from operonx.core.loggings import LOGGER

__all__ = ["register_transport", "resolve_transport", "transport_names"]

#: name -> factory(spec) -> Transport
_TRANSPORTS: Dict[str, Callable[..., Any]] = {}


def register_transport(name: str, factory: Callable[..., Any], *, replace: bool = False) -> None:
    """Register a transport under a short `kind` name.

    Args:
        name: What `[[serve]] kind = "..."` will say.
        factory: Called with the :class:`~operonx.core.manifest.ServeSpec`
            and returns a :class:`~operonx.core.serve.protocol.Transport`.
        replace: Permit overwriting an existing name. Off by default so a
            project shadowing a built-in has to mean it.
    """
    if not name or ":" in name:
        raise ValueError(f"transport name {name!r} must be a bare name, not an import path")
    if name in _TRANSPORTS and not replace:
        raise ValueError(
            f"transport {name!r} is already registered; pass replace=True to override it"
        )
    _TRANSPORTS[name] = factory
    LOGGER.debug(f"[serve] transport registered: {name}")


def transport_names() -> tuple:
    return tuple(sorted(_TRANSPORTS))


def resolve_transport(kind: str) -> Callable[..., Any]:
    """Find the factory for a `kind`.

    A bare name is looked up in the registry; anything containing ``:`` is
    imported as ``module:attribute``. The import path exists so a project
    can point at its own class without registering it first — the shortest
    possible distance between "I have a transport" and "operonx serves it".
    """
    if ":" in kind:
        module_name, _, attr = kind.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ImportError(f"transport {kind!r}: cannot import {module_name!r} — {exc}") from exc
        try:
            return getattr(module, attr)
        except AttributeError:
            raise ImportError(f"transport {kind!r}: {module_name!r} has no {attr!r}") from None

    factory = _TRANSPORTS.get(kind)
    if factory is None:
        known = ", ".join(transport_names()) or "none registered"
        raise LookupError(
            f"unknown transport kind {kind!r}. Registered: {known}. "
            f"Use a `module:Class` path for a transport of your own."
        )
    return factory


def load_object(path: str) -> Any:
    """Import a ``module:attribute`` reference, for `graph`/`on_session`/`app`."""
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"{path!r} is not `module:attribute`")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ImportError(f"{module_name!r} has no {attr!r}") from None
