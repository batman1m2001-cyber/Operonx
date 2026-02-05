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
from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from hush.core.states import MemoryState
    from hush.core.tracers.store import TraceStore


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

    def _merge_tags(self, state: 'MemoryState') -> List[str]:
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

    def flush_in_background(self, workflow_name: str, state: 'MemoryState') -> None:
        """Mark trace as complete and trigger background flushing.

        With incremental writes, trace data is already in SQLite (written during
        node execution via state.record_trace_metadata()). This method:
        1. Merges static (tracer) and dynamic (state) tags
        2. Marks the request as complete (status: writing -> pending)
        3. Background process will pick up and flush automatically

        All operations are non-blocking.

        For legacy mode (no trace_store), falls back to batch insert.

        Args:
            workflow_name: Name of the workflow
            state: MemoryState object containing execution data
        """
        from hush.core.loggings import LOGGER
        from hush.core.tracers.store import get_store

        try:
            # Merge static and dynamic tags
            merged_tags = self._merge_tags(state)

            if state.has_trace_store:
                # New mode: traces already written incrementally
                # Just mark complete - background process handles flushing
                store = state._trace_store
                store.mark_request_complete(
                    request_id=state.request_id,
                    tracer_type=self.__class__.__name__,
                    tracer_config=self._get_tracer_config(),
                    tags=merged_tags if merged_tags else None,
                )
            else:
                # Legacy mode: batch insert from in-memory data
                store = get_store()
                self._insert_legacy_traces(store, workflow_name, state, merged_tags)

            LOGGER.debug(
                "[title]\\[%s][/title] Trace ready for flush: [highlight]%s[/highlight]",
                state.request_id,
                workflow_name,
            )

        except Exception as e:
            LOGGER.error(
                "[title]\\[%s][/title] Failed to prepare trace for [highlight]%s[/highlight]: %s",
                state.request_id,
                workflow_name,
                str(e)
            )

    def _insert_legacy_traces(
        self,
        store: 'TraceStore',
        workflow_name: str,
        state: 'MemoryState',
        tags: Optional[List[str]] = None,
    ) -> None:
        """Insert traces from legacy in-memory storage.

        This is used when state doesn't have a trace_store (backwards compatibility).

        Args:
            store: TraceStore to insert into
            workflow_name: Name of the workflow
            state: MemoryState with in-memory trace data
            tags: Optional merged tags (static + dynamic)
        """
        execution_order = state.execution_order
        trace_metadata = state.trace_metadata

        for idx, execution in enumerate(execution_order):
            node_name = execution["node"]
            parent_name = execution.get("parent")
            context_id = execution.get("context_id")

            # Get trace data for this node
            trace_key = f"{node_name}:{context_id}" if context_id else node_name
            trace_data = trace_metadata.get(trace_key, {})

            # Extract fields from trace_data
            usage = trace_data.get("usage") or {}

            store.insert_node_trace(
                request_id=state.request_id,
                workflow_name=workflow_name,
                node_name=node_name,
                parent_name=parent_name,
                context_id=context_id,
                execution_order=idx,
                start_time=None,  # Legacy mode doesn't have timing
                end_time=None,
                duration_ms=None,
                input_data={},  # Would need to read from state
                output_data={},
                user_id=state.user_id,
                session_id=state.session_id,
                model=trace_data.get("model"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                cost_usd=trace_data.get("cost"),
                contain_generation=trace_data.get("contain_generation", False),
                metadata=trace_data.get("metadata"),
            )

        # Mark as complete with tags
        store.mark_request_complete(
            request_id=state.request_id,
            tracer_type=self.__class__.__name__,
            tracer_config=self._get_tracer_config(),
            tags=tags,
        )

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