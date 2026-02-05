"""Local tracer that stores traces in SQLite only.

This tracer is useful for:
- Local development and debugging
- Testing the tracing infrastructure
- Offline trace collection for later analysis

Traces are stored in the path specified by HUSH_TRACES_DB env var,
or ~/.hush/traces.db if not set.
"""

from typing import Any, Dict, List, Optional

from hush.core.tracers.base import BaseTracer, register_tracer


@register_tracer
class LocalTracer(BaseTracer):
    """Tracer that stores traces locally in SQLite without external flushing.

    This is the simplest tracer implementation - it just marks traces as
    complete and lets the background process write them to SQLite.
    The flush() method does nothing since traces are already persisted.

    Useful for:
    - Local development
    - Testing
    - Offline analysis via SQL queries

    Example:
        ```python
        from hush.core.tracers import LocalTracer

        tracer = LocalTracer(tags=["dev", "testing"])

        # Use with workflow engine
        engine = Hush(graph)
        result = await engine.run(inputs={...}, tracer=tracer)

        # Traces are now in HUSH_TRACES_DB (default: ~/.hush/traces.db)
        # Query with: SELECT * FROM traces WHERE request_id = '...'
        # Filter by tags: WHERE tags LIKE '%"dev"%'
        ```
    """

    def __init__(self, name: str = "local", tags: Optional[List[str]] = None):
        """Initialize LocalTracer.

        Args:
            name: Optional name for this tracer instance
            tags: Optional list of static tags for filtering/grouping traces
        """
        super().__init__(tags=tags)
        self.name = name

    def _get_tracer_config(self) -> Dict[str, Any]:
        """Return tracer configuration.

        Returns:
            Dict with tracer name
        """
        return {"name": self.name}

    @staticmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        """Flush traces to local SQLite viewer.

        For LocalTracer, traces are already in SQLite - this method just
        logs completion. The background worker will mark them as 'flushed'.

        Args:
            flush_data: Dictionary containing trace data
        """
        # Traces are already in SQLite, background worker marks them flushed
        # Just log for debugging
        pass

    def __repr__(self) -> str:
        """String representation."""
        return f"<LocalTracer name={self.name}>"