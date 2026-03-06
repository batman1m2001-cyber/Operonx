"""TraceCollector — pure post-processing step for extracting trace data.

The collector is completely separated from ops. Ops don't know about tracing.
After workflow execution, the collector walks the compiled graph for static
metadata and reads dynamic execution data from state.

Streaming-aware: derives kind, context lineage, yield counts, and spawned_by
from existing graph/scheduler data. Zero changes to core code required.
"""

import logging
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from hush.core.ops.graph.scheduler import _is_gen
from hush.core.states.cell import DEFAULT_CONTEXT
from hush.core.tracing.models import NodeStructure, TraceRecord, TraceSummary

LOGGER = logging.getLogger("hush.tracing")


def _is_stream_segment(seg: str) -> bool:
    """Check if a context segment is a stream segment like 's0', 's12'."""
    return isinstance(seg, str) and len(seg) > 1 and seg[0] == "s" and seg[1:].isdigit()


def _is_loop_segment(seg: str) -> bool:
    """Check if a context segment is a loop iteration like 'loop_0', 'loop_5'."""
    return isinstance(seg, str) and seg.startswith("loop_")


class TraceCollector:
    """Extracts trace data from graph (static) + state (dynamic).

    Static data: op_type, parent_name, contain_generation — from op @properties.
    Dynamic data: inputs, outputs, timing, model, usage, cost — from state cells.
    Streaming data: kind, context lineage, yield_count — derived from graph metadata.

    Usage:
        collector = TraceCollector()
        trace_data = collector.collect(graph, state)
    """

    def collect(self, graph: Any, state: Any) -> Dict[str, Any]:
        """Extract all trace data after workflow execution.

        Args:
            graph: Root GraphOp (compiled workflow graph)
            state: MemoryState after execution completes

        Returns:
            Dict with request_id, workflow_name, graph_structure, records, summary.
        """
        # 1. Build op lookup: full_name → op object
        op_map: Dict[str, Any] = {}
        self._build_op_map(graph, op_map)

        # 2. Build per-graph streaming metadata
        graph_meta: Dict[str, Dict] = {}
        self._build_graph_meta(graph, graph_meta)

        # 3. Static: graph structure from op @properties
        graph_structure = self._collect_graph_structure(op_map)

        # 4. Dynamic: execution records from state
        records = self._collect_records(op_map, graph_meta, state)

        # 5. Summary
        summary = self._build_summary(op_map, records)

        # 6. Build payload
        return {
            "request_id": state.request_id,
            "user_id": state.user_id,
            "session_id": state.session_id,
            "workflow_name": graph.name,
            "tags": list(state.tags) if state.tags else [],
            "graph_structure": [asdict(n) for n in graph_structure],
            "records": [asdict(r) for r in records],
            "summary": asdict(summary),
        }

    # =========================================================================
    # Graph Walking
    # =========================================================================

    def _build_op_map(self, op: Any, result: Dict[str, Any]) -> None:
        """Recursively build full_name → op lookup from graph tree."""
        result[op.full_name] = op
        if hasattr(op, "_ops") and op._ops:
            for child in op._ops.values():
                self._build_op_map(child, result)

    def _build_graph_meta(self, graph: Any, result: Dict[str, Dict]) -> None:
        """Build per-graph streaming metadata for kind/lineage derivation.

        Each GraphOp has its own scheduler, stream_depths, and prevs.
        We collect these so the record builder can determine streaming context.
        """
        from hush.core.ops.graph.graph_op import GraphOp

        gen_ops: Set[str] = set()
        stream_depths = getattr(graph, "_stream_depths", {})
        stream_contexts: List[Tuple] = []
        if graph._scheduler is not None:
            stream_contexts = getattr(graph._scheduler, "stream_contexts", [])

        for name, op in graph._ops.items():
            if _is_gen(op):
                gen_ops.add(op.full_name)
            if isinstance(op, GraphOp):
                self._build_graph_meta(op, result)

        result[graph.full_name] = {
            "gen_ops": gen_ops,
            "stream_depths": stream_depths,
            "stream_contexts": stream_contexts,
            "prevs": dict(graph.prevs),
            "ops": graph._ops,
        }

    # =========================================================================
    # Static Structure
    # =========================================================================

    def _collect_graph_structure(self, op_map: Dict[str, Any]) -> List[NodeStructure]:
        """Extract static metadata from all ops in the graph."""
        return [
            NodeStructure(
                op_name=op.full_name,
                op_type=getattr(op, "type", "default"),
                parent_name=op.parent.full_name if op.parent else None,
                contain_generation=op.contain_generation,
            )
            for op in op_map.values()
        ]

    # =========================================================================
    # Dynamic Records
    # =========================================================================

    def _determine_kind(
        self, op_full_name: str, ctx: Tuple, gen_ops: Set[str]
    ) -> str:
        """Determine record kind from context shape and op type."""
        if not ctx:
            return "batch"
        last_seg = ctx[-1]
        if op_full_name in gen_ops and not _is_stream_segment(last_seg):
            return "generator"
        if _is_loop_segment(last_seg):
            return "loop_iter"
        if _is_stream_segment(last_seg):
            return "stream_item"
        return "batch"

    def _find_spawner(
        self,
        op_short_name: str,
        prevs: Dict[str, List[str]],
        ops: Dict[str, Any],
        gen_ops: Set[str],
    ) -> Optional[str]:
        """Walk predecessors (BFS) to find the nearest generator that spawned this context."""
        visited: Set[str] = set()
        queue = list(prevs.get(op_short_name, []))
        while queue:
            pred = queue.pop(0)
            if pred in visited:
                continue
            visited.add(pred)
            pred_op = ops.get(pred)
            if pred_op and pred_op.full_name in gen_ops:
                return pred_op.full_name
            queue.extend(prevs.get(pred, []))
        return None

    def _count_yields(
        self, gen_ctx: Tuple, stream_contexts: List[Tuple]
    ) -> int:
        """Count how many stream contexts were spawned by a generator at gen_ctx."""
        prefix_len = len(gen_ctx)
        return sum(
            1
            for sc in stream_contexts
            if len(sc) == prefix_len + 1 and sc[:prefix_len] == gen_ctx
        )

    def _format_time(self, t: Any) -> Optional[str]:
        """Format a time value to ISO string."""
        if t is None:
            return None
        if isinstance(t, str):
            return t
        return t.isoformat()

    def _collect_records(
        self,
        op_map: Dict[str, Any],
        graph_meta: Dict[str, Dict],
        state: Any,
    ) -> List[TraceRecord]:
        """Extract dynamic execution data from state."""
        records = []

        for op_name, op in op_map.items():
            # Find parent graph's metadata
            parent_name = op.parent.full_name if op.parent else None
            meta = graph_meta.get(parent_name, {})
            gen_ops = meta.get("gen_ops", set())
            stream_depths = meta.get("stream_depths", {})
            stream_contexts = meta.get("stream_contexts", [])
            prevs = meta.get("prevs", {})
            ops = meta.get("ops", {})

            for ctx, start_time in state.iter_executed(op_name):
                kind = self._determine_kind(op_name, ctx, gen_ops)

                # Read I/O (exclude internal $-prefixed keys)
                inputs = {
                    v: state[op_name, v, ctx]
                    for v in (op.inputs or {})
                    if not v.startswith("$")
                }
                outputs = {
                    v: state[op_name, v, ctx]
                    for v in (op.outputs or {})
                    if not v.startswith("$")
                }

                # Timing
                end_time = state[op_name, "end_time", ctx]
                duration_ms = state[op_name, "duration_ms", ctx]

                # LLM-specific
                model = outputs.get("model_used") if op.contain_generation else None
                usage = outputs.get("tokens_used") if op.contain_generation else None
                cost = state[op_name, "cost_usd", ctx]

                # Context lineage
                parent_context = ctx[:-1] if len(ctx) > 1 else None
                depth = stream_depths.get(op.name, 0)

                # Spawned by: find generator for stream contexts
                spawned_by = None
                if kind == "stream_item":
                    spawned_by = self._find_spawner(op.name, prevs, ops, gen_ops)

                # Yield count for generators
                yield_count = None
                if kind == "generator":
                    yield_count = self._count_yields(ctx, stream_contexts)

                records.append(
                    TraceRecord(
                        op_name=op_name,
                        context=ctx,
                        kind=kind,
                        inputs=inputs,
                        outputs=outputs,
                        start_time=self._format_time(start_time),
                        end_time=self._format_time(end_time),
                        duration_ms=duration_ms,
                        yield_count=yield_count,
                        depth=depth,
                        parent_context=parent_context,
                        spawned_by=spawned_by,
                        model=model,
                        usage=usage,
                        cost=cost,
                    )
                )

        # Sort by start_time to reconstruct execution order
        records.sort(key=lambda r: r.start_time or "")
        return records

    # =========================================================================
    # Summary
    # =========================================================================

    def _build_summary(
        self, op_map: Dict[str, Any], records: List[TraceRecord]
    ) -> TraceSummary:
        """Aggregate records into a top-level summary."""
        total_duration = 0.0
        total_yields = 0
        loop_iterations = 0
        error_count = 0
        stream_count = 0

        for r in records:
            if r.duration_ms:
                total_duration += r.duration_ms
            if r.kind == "generator":
                stream_count += 1
                total_yields += r.yield_count or 0
            elif r.kind == "loop_iter":
                loop_iterations += 1

        return TraceSummary(
            total_ops=len(op_map),
            total_records=len(records),
            total_duration_ms=round(total_duration, 2),
            stream_count=stream_count,
            total_yields=total_yields,
            loop_iterations=loop_iterations,
            error_count=error_count,
        )
