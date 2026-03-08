"""FlushWorker — background thread pool for trace collection and flushing.

Both collect (CPU-bound) and flush (I/O-bound) run in the thread pool,
never blocking the main async thread.
"""

import atexit
import logging
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from hush.core.tracing.base import Tracer
from hush.core.tracing.collector import TraceCollector

LOGGER = logging.getLogger("hush.tracing")

_worker: "FlushWorker | None" = None


class FlushWorker:
    """Thread pool for trace collection and flushing.

    Runs both TraceCollector.collect() and tracer.flush() in background threads
    so engine.run() returns immediately without blocking.
    """

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="hush-trace"
        )
        self._futures: List[Future] = []

    def submit(self, tracers: List[Tracer], graph: Any, state: Any) -> Future:
        """Submit a collect-and-flush task to the thread pool.

        Returns a Future that callers can optionally wait on to check for errors.
        The background thread will:
        1. Run TraceCollector.collect_tree(graph, state)
        2. For each tracer, merge tags and call tracer.flush()

        Args:
            tracers: List of Tracer instances to flush to
            graph: Root GraphOp (compiled workflow graph)
            state: MemoryState after execution completes

        Returns:
            Future that resolves when flush completes. Call .result() to
            re-raise any exception from the background thread.
        """
        future = self._executor.submit(self._collect_and_flush, tracers, graph, state)
        self._futures.append(future)
        return future

    def _collect_and_flush(self, tracers: List[Tracer], graph: Any, state: Any) -> None:
        """Collect trace data and flush to all tracers.

        Errors are logged AND re-raised so they propagate through the Future.
        """
        errors: List[Exception] = []

        try:
            # 1. Collect trace tree (CPU-bound, microseconds)
            collector = TraceCollector()
            trace_data = collector.collect_tree(graph, state)
        except Exception as e:
            LOGGER.exception("Failed to collect trace data")
            raise

        # 2. Flush to each tracer with merged tags
        for tracer in tracers:
            try:
                # Merge: dynamic tags (from state) + static tags (from tracer)
                merged = _merge_tags(trace_data.get("tags", []), tracer.tags)
                # Apply stream sampling per tracer's limit
                nodes = trace_data.get("nodes", [])
                limit = getattr(tracer, "_stream_trace_limit", None)
                sampled_nodes = _sample_stream_nodes(nodes, limit)
                # Create a copy with merged tags and sampled nodes
                data = {**trace_data, "tags": merged if merged else None, "nodes": sampled_nodes}
                tracer.flush(data)
            except Exception as e:
                LOGGER.exception("Failed to flush traces to %s", type(tracer).__name__)
                errors.append(e)

        if errors:
            msg = f"Flush failed for {len(errors)} tracer(s): {errors}"
            raise RuntimeError(msg) from errors[0]

    def wait(self, timeout: Optional[float] = None) -> List[Exception]:
        """Wait for all pending flush tasks to complete.

        Args:
            timeout: Max seconds to wait per future. None = wait forever.

        Returns:
            List of exceptions from failed flushes (empty if all succeeded).
        """
        errors = []
        for future in self._futures:
            try:
                future.result(timeout=timeout)
            except Exception as e:
                errors.append(e)
        self._futures.clear()
        return errors

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the thread pool."""
        self._executor.shutdown(wait=wait)


def _sample_stream_nodes(nodes: List[Dict[str, Any]], limit: Optional[int]) -> List[Dict[str, Any]]:
    """Apply stream_trace_limit sampling to stream_item nodes.

    Caps stream_item nodes per spawned_by generator, then removes
    orphaned stream_context synthetic nodes that lost all children.

    Args:
        nodes: Full list of TraceNode dicts (from collect_tree).
        limit: Max stream_items to keep per spawned_by generator.
            None = keep all, 0 = drop all stream_items.

    Returns:
        Filtered node list. Non-stream_item nodes pass through unchanged
        (unless they're orphaned synthetic contexts).
    """
    if limit is None:
        return nodes

    # First pass: filter stream_items by spawner limit
    counts: Dict[Optional[str], int] = defaultdict(int)
    kept_keys: set = set()
    filtered = []
    for n in nodes:
        if n.get("kind") != "stream_item":
            filtered.append(n)
            kept_keys.add(n["trace_key"])
            continue
        spawner = (n.get("metadata") or {}).get("spawned_by")
        counts[spawner] += 1
        if counts[spawner] <= limit:
            filtered.append(n)
            kept_keys.add(n["trace_key"])

    # Second pass: remove orphaned stream_context nodes (no children kept)
    result = []
    for n in filtered:
        if n.get("kind") == "stream_context":
            has_child = any(
                c["trace_key"] in kept_keys and c.get("parent_trace_key") == n["trace_key"]
                for c in filtered
                if c["trace_key"] != n["trace_key"]
            )
            if not has_child:
                continue
        result.append(n)

    return result


def _merge_tags(dynamic_tags: List[str], static_tags: List[str]) -> List[str]:
    """Merge dynamic (from state) and static (from tracer) tags, deduped.

    Static tags come first, then unique dynamic tags are appended.
    Same logic as existing BaseTracer._merge_tags().
    """
    merged = list(static_tags)
    for tag in dynamic_tags:
        if tag not in merged:
            merged.append(tag)
    return merged


def get_flush_worker() -> FlushWorker:
    """Get or create the global FlushWorker singleton.

    The worker is created lazily on first call and registered for
    shutdown at interpreter exit.
    """
    global _worker
    if _worker is None:
        _worker = FlushWorker()
        atexit.register(_worker.shutdown)
    return _worker
