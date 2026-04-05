"""Shared helpers for provider ops.

Kept separate to avoid repeating the hub-lookup try/except in every op.
"""

from hush.core.registry import ResourceHub, get_hub


def resolve_hub():
    """Return the active ResourceHub singleton.

    Tries ``ResourceHub.instance()`` first (set by hush-serve).
    Falls back to ``get_hub()`` (set by ``set_global_hub()`` / ``Hush(resources=...)``).
    Raises ``RuntimeError`` with a helpful message if neither is configured.
    """
    try:
        return ResourceHub.instance()
    except RuntimeError:
        return get_hub()
