"""Shared helpers for provider ops."""

from operonx.core.registry import ResourceHub


def resolve_hub() -> ResourceHub:
    """Return the active ResourceHub singleton.

    Raises ``RuntimeError`` if no hub has been loaded. Construct an
    ``Operon`` engine to install one.
    """
    return ResourceHub.instance()
