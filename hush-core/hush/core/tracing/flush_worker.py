"""FlushWorker — background thread pool for trace collection and flushing.

Both collect (CPU-bound) and flush (I/O-bound) run in the thread pool,
never blocking the main async thread.
"""

import atexit
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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

    def submit(self, tracers: List[Tracer], graph: Any, state: Any) -> None:
        """Submit a collect-and-flush task to the thread pool.

        Returns immediately. The background thread will:
        1. Run TraceCollector.collect(graph, state)
        2. For each tracer, merge tags and call tracer.flush()

        Args:
            tracers: List of Tracer instances to flush to
            graph: Root GraphOp (compiled workflow graph)
            state: MemoryState after execution completes
        """
        self._executor.submit(self._safe_collect_and_flush, tracers, graph, state)

    def _safe_collect_and_flush(self, tracers: List[Tracer], graph: Any, state: Any) -> None:
        """Collect trace data and flush to all tracers, with error handling."""
        try:
            # 1. Collect trace data (CPU-bound, microseconds)
            collector = TraceCollector()
            trace_data = collector.collect(graph, state)

            # 2. Flush to each tracer with merged tags
            for tracer in tracers:
                try:
                    # Merge: dynamic tags (from state) + static tags (from tracer)
                    merged = _merge_tags(trace_data.get("tags", []), tracer.tags)
                    # Apply stream sampling per tracer's limit
                    records = trace_data.get("records", [])
                    limit = getattr(tracer, "_stream_trace_limit", None)
                    sampled_records = _sample_stream_records(records, limit)
                    # Create a copy with merged tags and sampled records
                    data = {**trace_data, "tags": merged if merged else None, "records": sampled_records}
                    tracer.flush(data)
                except Exception:
                    LOGGER.exception("Failed to flush traces to %s", type(tracer).__name__)
        except Exception:
            LOGGER.exception("Failed to collect trace data")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the thread pool."""
        self._executor.shutdown(wait=wait)


def _sample_stream_records(
    records: List[Dict[str, Any]], limit: Optional[int]
) -> List[Dict[str, Any]]:
    """Apply stream_trace_limit sampling to stream_item records.

    Args:
        records: Full list of trace record dicts.
        limit: Max stream_items to keep per spawned_by generator.
            None = keep all, 0 = drop all stream_items.

    Returns:
        Filtered record list. Non-stream_item records pass through unchanged.
    """
    if limit is None:
        return records

    # Group stream_items by spawned_by, keep first N per generator
    counts: Dict[Optional[str], int] = defaultdict(int)
    result = []
    for r in records:
        if r.get("kind") != "stream_item":
            result.append(r)
            continue
        spawner = r.get("spawned_by")
        counts[spawner] += 1
        if counts[spawner] <= limit:
            result.append(r)

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
