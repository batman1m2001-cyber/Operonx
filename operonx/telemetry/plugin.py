"""Plugin: auto-register observability backends to ResourceHub.

Lets ``langfuse:...`` resource keys in ``resources.yaml`` resolve to a
``LangfuseClient`` instance. Imported by ``operonx.telemetry.__init__``
so registration happens transparently.
"""

from operonx.core.loggings import LOGGER

_registered = False


def register():
    """Register Langfuse config + client factory."""
    global _registered
    if _registered:
        return
    try:
        from operonx.core.registry import REGISTRY
        from operonx.telemetry.backends.langfuse import (
            LangfuseClient,
            LangfuseConfig,
        )

        REGISTRY.register(LangfuseConfig, lambda c: LangfuseClient(c))
        _registered = True
        LOGGER.debug("Telemetry plugin registered (langfuse).")
    except ImportError as e:
        LOGGER.warning(
            "Failed to register telemetry plugin: %s. "
            "ResourceHub integration will not be available.",
            str(e),
        )


def is_registered() -> bool:
    return _registered


# Auto-register on import
register()
