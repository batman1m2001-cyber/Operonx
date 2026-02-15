"""Base tracer for the new tracing system.

Tracers receive collected trace data and flush it to external backends.
Each tracer can have static tags that are merged with dynamic tags at flush time.
"""

from typing import Any, Dict, List, Optional


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

    def __init__(self, tags: Optional[List[str]] = None):
        self._tags = tags or []

    @property
    def tags(self) -> List[str]:
        """Static tags for this tracer instance."""
        return self._tags.copy()

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data to the backend.

        Called by FlushWorker in a background thread.

        Args:
            trace_data: Dict matching hush-eyes IngestRequest format.
                Tags are already merged (dynamic + static) before this call.
        """
        raise NotImplementedError
