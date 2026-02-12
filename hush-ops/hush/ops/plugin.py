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
    import hush.ops

    from hush.core.registry import get_hub
    client = get_hub().langfuse("default")
    ```
"""

from hush.core.loggings import LOGGER


class ObservabilityPlugin:
    """Plugin for registering observability backends to ResourceHub."""

    _registered = False

    @classmethod
    def register(cls):
        """Register all observability config classes and factory handlers."""
        if cls._registered:
            return

        try:
            from hush.core.registry import REGISTRY

            # Register Langfuse
            from hush.ops.backends.langfuse import (
                LangfuseClient,
                LangfuseConfig,
            )

            REGISTRY.register(LangfuseConfig, lambda c: LangfuseClient(c))

            # Register OpenTelemetry
            from hush.ops.backends.otel import (
                OTELClient,
                OTELConfig,
            )

            REGISTRY.register(OTELConfig, lambda c: OTELClient(c))

            cls._registered = True
            LOGGER.debug("ObservabilityPlugin registered successfully")

        except ImportError as e:
            LOGGER.warning(
                "Failed to register ObservabilityPlugin: %s. "
                "ResourceHub integration will not be available.",
                str(e),
            )

    @classmethod
    def is_registered(cls) -> bool:
        """Check if plugin is registered."""
        return cls._registered


# Auto-register on import
ObservabilityPlugin.register()
