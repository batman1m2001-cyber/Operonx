"""ConfigurableTracer — shared base for tracers that accept a direct config
or a ResourceHub resource name.

Extracts the duplicate __init__ validation, resource property, and
_get_client dispatch that were previously copied into LangfuseTracer and
OTELTracer.
"""

from typing import List, Optional

from hush.core.tracing import Tracer


class ConfigurableTracer(Tracer):
    """Base class for tracers backed by either a direct config or a ResourceHub resource.

    Subclasses must implement ``_make_client(config)`` to construct their
    backend client from a config object.  ``_get_client()`` dispatches
    automatically: direct config → ``_make_client(config)``; resource string
    → ``get_hub().get(resource)``.
    """

    def __init__(
        self,
        config=None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

    @property
    def resource(self) -> Optional[str]:
        return self._resource

    def _make_client(self, config):
        """Create the backend client from a config object. Override in subclasses."""
        raise NotImplementedError

    def _get_client(self):
        """Return the backend client from config or ResourceHub."""
        if self._config is not None:
            return self._make_client(self._config)
        from hush.core.registry import get_hub

        return get_hub().get(self._resource)
