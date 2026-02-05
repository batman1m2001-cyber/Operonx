"""Hush Core Tracers - Base tracing infrastructure for workflow observability.

This module provides the abstract base tracer and media utilities.
Concrete tracer implementations (Langfuse, OpenTelemetry, etc.) are in hush-observability.

Example:
    ```python
    from hush.core.tracers import BaseTracer, register_tracer, MediaAttachment

    # Create custom tracer
    @register_tracer
    class MyTracer(BaseTracer):
        def _get_tracer_config(self) -> Dict[str, Any]:
            return {"api_key": self.api_key}

        @staticmethod
        def flush(flush_data: Dict[str, Any]) -> None:
            # Send traces to your platform
            pass

    # Use media attachments in nodes
    attachment = MediaAttachment.from_bytes(
        content=image_bytes,
        content_type="image/png",
        attach_to="output"
    )
    ```
"""
from hush.core.tracers.base import (
    BaseTracer,
    register_tracer,
    get_registered_tracers,
)
from hush.core.tracers.media import (
    MEDIA_KEY,
    MAX_INLINE_SIZE,
    MediaAttachment,
    serialize_media_attachments,
)
from hush.core.tracers.store import (
    TraceStore,
    get_store,
    DEFAULT_DB_PATH,
)
from hush.core.tracers.local import LocalTracer

__all__ = [
    # Base tracer
    "BaseTracer",
    "register_tracer",
    "get_registered_tracers",
    # Media utilities
    "MEDIA_KEY",
    "MAX_INLINE_SIZE",
    "MediaAttachment",
    "serialize_media_attachments",
    # Store
    "TraceStore",
    "get_store",
    "DEFAULT_DB_PATH",
    # Local tracer
    "LocalTracer",
]