"""Abstract base tracer for workflow tracing.

This module provides the BaseTracer abstract class that all concrete tracer
implementations must inherit from. Tracers are responsible for collecting
and exporting workflow execution traces to observability platforms.

Traces are written to SQLite via a unified background process, then flushed
to external services (Langfuse, etc.) asynchronously.

Example:
    ```python
    from hush.core.tracers import BaseTracer, register_tracer

    @register_tracer
    class MyTracer(BaseTracer):
        def _get_tracer_config(self) -> Dict[str, Any]:
            return {"api_key": self.api_key}

        @staticmethod
        def flush(flush_data: Dict[str, Any]) -> None:
            # Send traces to your platform
            pass
    ```
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class BaseTracer(ABC):
    """Abstract base class for workflow tracers.

    Subclasses must implement:
        - flush(): The actual tracing logic
        - _get_tracer_config(): Returns tracer-specific config

    All trace operations are non-blocking - they are sent to a unified
    background process that handles:
        - Writing traces to SQLite
        - Flushing to external services
        - Retry on failure

    Tags:
        Tracers support both static and dynamic tags for filtering/grouping traces:
        - Static tags: Set at tracer initialization (e.g., tags=["prod", "ml-team"])
        - Dynamic tags: Added during execution via $tags in node return values

        Both are merged at flush time and stored in SQLite.
    """

    # Static tags for this tracer instance
    _tags: List[str]

    def __init__(self, tags: Optional[List[str]] = None):
        """Initialize base tracer.

        Args:
            tags: Optional list of static tags for this tracer instance
        """
        self._tags = tags or []

    @property
    def tags(self) -> List[str]:
        """Get static tags for this tracer."""
        return self._tags.copy()

    @classmethod
    def shutdown_worker(cls, timeout: float = 5.0) -> None:
        """Shutdown the background process gracefully.

        Args:
            timeout: Maximum time to wait for shutdown
        """
        from hush.core.background import shutdown_background

        shutdown_background()

    # Keep old name for backwards compatibility
    shutdown_executor = shutdown_worker

    @abstractmethod
    def _get_tracer_config(self) -> Dict[str, Any]:
        """Return tracer-specific configuration for serialization.

        This config will be stored in the database and passed to flush().

        Returns:
            Dictionary containing tracer configuration
        """
        pass

    def _merge_tags(self, state: "MemoryState") -> List[str]:
        """Merge static tracer tags with dynamic state tags.

        Args:
            state: MemoryState containing dynamic tags

        Returns:
            Combined list of unique tags (static first, then dynamic)
        """
        merged = list(self._tags)  # Start with static tags
        for tag in state.tags:
            if tag not in merged:
                merged.append(tag)
        return merged

    @staticmethod
    @abstractmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        """Execute the flush logic.

        This method is called by the background process with reconstructed
        flush_data from the SQLite database.

        Args:
            flush_data: Dictionary containing all data needed for flushing
        """
        pass


# Registry of tracer types for subprocess dispatch
_TRACER_REGISTRY: Dict[str, type] = {}


def register_tracer(tracer_cls: type) -> type:
    """Decorator to register a tracer class for subprocess dispatch.

    Args:
        tracer_cls: The tracer class to register

    Returns:
        The registered tracer class

    Example:
        ```python
        @register_tracer
        class MyTracer(BaseTracer):
            ...
        ```
    """
    _TRACER_REGISTRY[tracer_cls.__name__] = tracer_cls
    return tracer_cls


def get_registered_tracers() -> Dict[str, type]:
    """Get all registered tracer classes.

    Returns:
        Dictionary mapping tracer names to their classes
    """
    return _TRACER_REGISTRY.copy()
