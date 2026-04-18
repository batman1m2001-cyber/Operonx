"""FlushWorker — background thread pool for trace collection and flushing.

Both collect (CPU-bound) and flush (I/O-bound) run in the thread pool,
never blocking the main async thread.
"""

import atexit
import logging
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from operon.core.tracing.base import Tracer
from operon.core.tracing.collector import TraceCollector

LOGGER = logging.getLogger("operon.tracing")

_worker: "FlushWorker | None" = None


class FlushWorker:
    """Thread pool for trace collection and flushing.

    Runs both TraceCollector.collect() and tracer.flush() in background threads
    so engine.run() returns immediately without blocking.
    """

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="operon-trace"
        )
        self._futures: List[Future] = []

    def submit(self, tracers: List[Tracer], collector: TraceCollector, state: Any) -> Future:
        """Submit a collect-and-flush task to the thread pool.

        Returns a Future that callers can optionally wait on to check for errors.
        The background thread will:
        1. Run collector.collect(state)
        2. For each tracer, merge tags and call tracer.flush()

        Args:
            tracers: List of Tracer instances to flush to
            collector: Pre-built TraceCollector (graph metadata already computed)
            state: MemoryState after execution completes

        Returns:
            Future that resolves when flush completes. Call .result() to
            re-raise any exception from the background thread.
        """
        self._futures = [f for f in self._futures if not f.done()]
        future = self._executor.submit(self._collect_and_flush, tracers, collector, state)
        self._futures.append(future)
        return future

    def _collect_and_flush(
        self, tracers: List[Tracer], collector: TraceCollector, state: Any
    ) -> None:
        """Collect trace data and flush to all tracers.

        Errors are logged AND re-raised so they propagate through the Future.
        """
        errors: List[Exception] = []

        try:
            # 1. Collect trace tree (CPU-bound, microseconds)
            trace_data = collector.collect(state)
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
                # Apply trace filter (exclude noisy ops, skip empty, etc.)
                tf = getattr(tracer, "trace_filter", None)
                filtered_nodes = tf.apply(sampled_nodes) if tf else sampled_nodes
                # Create a copy with merged tags and filtered nodes
                data = {**trace_data, "tags": merged if merged else None, "nodes": filtered_nodes}
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
    """Apply stream_trace_limit sampling to context groups per generator.

    For each graph that contains generators, caps the number of top-level
    stream_context groups. Excess context groups and all their descendants
    are removed.

    Args:
        nodes: Full list of TraceNode dicts (from collect_tree).
        limit: Max context groups to keep per generator parent.
            None = keep all, 0 = drop all context groups.

    Returns:
        Filtered node list. Generator and non-stream nodes pass through
        unchanged.
    """
    if limit is None:
        return nodes

    # Find parents that contain generators
    gen_parents: set = set()
    for n in nodes:
        if n.get("kind") == "generator":
            parent = n.get("parent_trace_key")
            if parent:
                gen_parents.add(parent)

    if not gen_parents:
        return nodes

    # Count top-level stream_context groups per generator parent
    counts: Dict[str, int] = defaultdict(int)
    remove_keys: set = set()

    for n in nodes:
        if n.get("kind") == "stream_context" and n.get("parent_trace_key") in gen_parents:
            parent = n["parent_trace_key"]
            counts[parent] += 1
            if counts[parent] > limit:
                remove_keys.add(n["trace_key"])

    if not remove_keys:
        return nodes

    # Cascade: remove all descendants of removed context groups
    children_map: Dict[str, list] = defaultdict(list)
    for n in nodes:
        p = n.get("parent_trace_key")
        if p:
            children_map[p].append(n["trace_key"])

    queue = list(remove_keys)
    while queue:
        k = queue.pop(0)
        for child in children_map.get(k, []):
            remove_keys.add(child)
            queue.append(child)

    return [n for n in nodes if n["trace_key"] not in remove_keys]


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
