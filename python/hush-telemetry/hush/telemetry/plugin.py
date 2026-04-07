"""Plugin for auto-registering observability backends to ResourceHub.

This plugin registers config classes and factory handlers for all
observability backends, allowing them to be loaded from resources.yaml.

Example:
    ```yaml
    # resources.yaml
    langfuse:default:
      type: langfuse
      public_key: pk-...
      secret_key: sk-...
      host: https://cloud.langfuse.com
    ```

    ```python
    # Auto-registration happens on import
    import hush.telemetry

    from hush.core.registry import get_hub
    client = get_hub().get("langfuse:default")
    ```
"""

from hush.core.loggings import LOGGER

_registered = False


def register():
    """Register all observability config classes and factory handlers."""
    global _registered
    if _registered:
        return

    try:
        from hush.core.registry import REGISTRY

        # Register Langfuse
        from hush.telemetry.backends.langfuse import (
            LangfuseClient,
            LangfuseConfig,
        )

        REGISTRY.register(LangfuseConfig, lambda c: LangfuseClient(c))

        # Register OpenTelemetry
        from hush.telemetry.backends.otel import (
            OTELClient,
            OTELConfig,
        )

        REGISTRY.register(OTELConfig, lambda c: OTELClient(c))

        _registered = True
        LOGGER.debug("Observability plugin registered successfully")

    except ImportError as e:
        LOGGER.warning(
            "Failed to register observability plugin: %s. "
            "ResourceHub integration will not be available.",
            str(e),
        )


def is_registered() -> bool:
    """Check if plugin is registered."""
    return _registered


# Auto-register on import
register()
