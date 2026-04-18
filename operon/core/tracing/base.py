"""Base tracer for the new tracing system.

Tracers receive collected trace data and flush it to external backends.
Each tracer can have static tags that are merged with dynamic tags at flush time.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from operon.core.tracing.trace_filter import TraceFilter


class Tracer:
    """Base class for tracers in the new tracing system.

    Subclasses implement flush() to send trace data to their backend.
    Static tags are set at construction and merged with dynamic tags by FlushWorker.

    Example:
        class MyTracer(Tracer):
            def flush(self, trace_data: dict) -> None:
                requests.post("https://my-backend/ingest", json=trace_data)

        tracer = MyTracer(tags=["prod", "ml-team"])
    """

    def __init__(
        self,
        tags: Optional[List[str]] = None,
        stream_trace_limit: Optional[int] = 100,
        trace_filter: Optional["TraceFilter"] = None,
    ):
        self._tags = tags or []
        self._stream_trace_limit = stream_trace_limit
        self._trace_filter = trace_filter

    @property
    def tags(self) -> List[str]:
        """Static tags for this tracer instance."""
        return self._tags.copy()

    @property
    def trace_filter(self) -> Optional["TraceFilter"]:
        """Optional filter to exclude noisy ops from traces."""
        return self._trace_filter

    def to_config_dict(self) -> Optional[Dict[str, Any]]:
        """Return a serializable config dict for the Rust backend.

        Override in tracers that support the Rust backend (e.g. LangfuseTracer,
        OperonEyesTracer). Return ``None`` (default) to indicate no Rust support.
        """
        return None

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data to the backend.

        Called by FlushWorker in a background thread.

        Args:
            trace_data: Dict matching ui-operon-eyes IngestRequest format.
                Tags are already merged (dynamic + static) before this call.
        """
        raise NotImplementedError
