"""ConfigurableTracer — shared base for tracers that accept a direct config
or a ResourceHub resource name.

Extracts the duplicate __init__ validation, resource property, and
_get_client dispatch that were previously copied into LangfuseTracer and
OTELTracer.
"""

import logging
from typing import TYPE_CHECKING, List, Optional

from hush.core.tracing import Tracer

if TYPE_CHECKING:
    from hush.core.tracing.trace_filter import TraceFilter

LOGGER = logging.getLogger("hush.tracing")


class ConfigurableTracer(Tracer):
    """Base class for tracers backed by either a direct config or a ResourceHub resource.

    Subclasses must implement ``_make_client(config)`` to construct their
    backend client from a config object.  ``_get_client()`` dispatches
    automatically: direct config → ``_make_client(config)``; resource string
    → ``get_hub().get(resource)``.

    If ``trace_filter`` is not passed explicitly, and the resource's YAML
    config contains a ``trace_filter`` dict, it is auto-parsed into a
    :class:`TraceFilter` instance.
    """

    def __init__(
        self,
        config=None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace_filter: Optional["TraceFilter"] = None,
    ):
        super().__init__(tags=tags, trace_filter=trace_filter)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

        # Auto-load trace_filter from YAML config if not explicitly provided
        if self._trace_filter is None:
            self._trace_filter = self._load_trace_filter()

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

    def _load_trace_filter(self) -> Optional["TraceFilter"]:
        """Try loading trace_filter from config or resource YAML.

        Checks direct config first (has .trace_filter attribute), then
        falls back to ResourceHub.get_config() for resource-based tracers.
        """
        from hush.core.tracing.trace_filter import TraceFilter

        # Direct config object — check for trace_filter attribute
        if self._config is not None:
            tf_dict = getattr(self._config, "trace_filter", None)
            if tf_dict and isinstance(tf_dict, dict):
                LOGGER.debug("TraceFilter loaded from direct config: %s", tf_dict)
                return TraceFilter.from_dict(tf_dict)
            return None

        # Resource-based — load raw config from ResourceHub
        if self._resource is not None:
            try:
                from hush.core.registry import get_hub

                config = get_hub().get_config(self._resource)
                raw = config if isinstance(config, dict) else config.model_dump()
                tf_dict = raw.get("trace_filter")
                if tf_dict and isinstance(tf_dict, dict):
                    LOGGER.debug(
                        "TraceFilter loaded from resource '%s': %s",
                        self._resource,
                        tf_dict,
                    )
                    return TraceFilter.from_dict(tf_dict)
            except Exception as exc:
                LOGGER.debug(
                    "Could not load trace_filter from resource '%s': %s",
                    self._resource,
                    exc,
                )

        return None
