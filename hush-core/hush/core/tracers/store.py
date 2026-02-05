"""SQLite-based trace store for incremental trace writes.

This module provides a TraceStore that writes traces via the unified
background process, making all writes non-blocking.

Traces are written incrementally during workflow execution, not batched at the end.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from hush.core.background import get_background, DEFAULT_DB_PATH

__all__ = ["TraceStore", "get_store", "DEFAULT_DB_PATH"]


class TraceStore:
    """SQLite-based storage for workflow traces with non-blocking writes.

    All writes are sent to the background process, making them completely
    non-blocking for the main workflow execution.

    This provides:
    - Zero latency impact on workflow execution
    - Lower memory usage (trace data not kept in MemoryState)
    - Crash resilience (background process writes to durable storage)
    - Real-time observability

    Example:
        store = TraceStore()

        # During node execution - write asynchronously (non-blocking)
        store.insert_node_trace(
            request_id="req-123",
            workflow_name="my-workflow",
            node_name="llm",
            parent_name="my-workflow",
            ...
        )

        # At workflow end - mark complete (non-blocking)
        store.mark_request_complete("req-123", tracer_type, tracer_config)
    """

    __slots__ = ['_db_path']

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize TraceStore.

        Args:
            db_path: Path to SQLite database. Defaults to HUSH_TRACES_DB env var
                     or ~/.hush/traces.db if not set.
        """
        self._db_path = db_path or DEFAULT_DB_PATH

    def insert_node_trace(
        self,
        request_id: str,
        workflow_name: str,
        node_name: str,
        node_type: Optional[str] = None,
        parent_name: Optional[str] = None,
        context_id: Optional[str] = None,
        execution_order: int = 0,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        duration_ms: Optional[float] = None,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        contain_generation: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert a single node trace (non-blocking).

        Sends the trace data to the background process for writing.
        This method returns immediately without waiting for the write.

        Args:
            request_id: Unique request identifier
            workflow_name: Name of the workflow
            node_name: Full name of the node
            node_type: Type of node (e.g., llm, branch, for, while, code)
            parent_name: Parent node name (None for root)
            context_id: Context ID for iteration nodes
            execution_order: Order of execution (0-indexed)
            start_time: ISO format start time
            end_time: ISO format end time
            duration_ms: Execution duration in milliseconds
            input_data: Input data dict
            output_data: Output data dict
            user_id: User identifier
            session_id: Session identifier
            model: LLM model name
            prompt_tokens: Input token count
            completion_tokens: Output token count
            total_tokens: Total token count
            cost_usd: Cost in USD
            contain_generation: Whether node contains LLM generation
            metadata: Additional metadata
        """
        bg = get_background(self._db_path)
        bg.write_trace(
            request_id=request_id,
            workflow_name=workflow_name,
            node_name=node_name,
            node_type=node_type,
            parent_name=parent_name,
            context_id=context_id,
            execution_order=execution_order,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            input_data=input_data,
            output_data=output_data,
            user_id=user_id,
            session_id=session_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            contain_generation=contain_generation,
            metadata=metadata,
        )

    def mark_request_complete(
        self,
        request_id: str,
        tracer_type: str,
        tracer_config: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> None:
        """Mark all traces for a request as ready for flushing (non-blocking).

        Sends the complete signal to the background process.
        This method returns immediately without waiting.

        Args:
            request_id: The request to mark complete
            tracer_type: Type of tracer (e.g., "LangfuseTracer")
            tracer_config: Tracer configuration dict
            tags: Optional list of tags for filtering/grouping traces
        """
        bg = get_background(self._db_path)
        bg.mark_complete(
            request_id=request_id,
            tracer_type=tracer_type,
            tracer_config=tracer_config,
            tags=tags,
        )


# Global store instance
_store: Optional[TraceStore] = None


def get_store(db_path: Optional[Path] = None) -> TraceStore:
    """Get global TraceStore instance.

    Args:
        db_path: Optional path to database

    Returns:
        TraceStore instance
    """
    global _store
    if _store is None:
        _store = TraceStore(db_path)
    return _store