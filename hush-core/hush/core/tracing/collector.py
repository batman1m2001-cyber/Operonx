"""TraceCollector — post-processing step for extracting trace data.

The collector is completely separated from ops. Ops don't know about tracing.
After workflow execution, the collector walks the compiled graph for static
metadata and reads dynamic execution data from state.

Each (op, context) execution becomes a TraceNode. Streaming ops are grouped
under synthetic [N] context nodes for hierarchical visualization.

Usage:
    collector = TraceCollector(graph)   # precomputes graph metadata + topo order
    trace_data = collector.collect(state)  # per-run: reads state, builds tree
"""

import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from hush.core.ops.graph.scheduler import _is_gen
from hush.core.tracing.models import TraceNode, TraceSummary
from hush.core.utils.algo import build_children, tree_walk
from hush.core.utils.algo import topo_rank as compute_topo_rank

LOGGER = logging.getLogger("hush.tracing")


def _is_loop_segment(seg: str) -> bool:
    """Check if a context segment is a loop iteration like 'loop_0', 'loop_5'."""
    return isinstance(seg, str) and seg.startswith("loop_")


def _is_stream_segment(seg: str) -> bool:
    """Check if a context segment is a stream index like '[0]', '[12]'."""
    return isinstance(seg, str) and seg.startswith("[") and seg.endswith("]")


def _ctx_to_str(ctx: Tuple) -> Optional[str]:
    """Convert a context tuple to dot-separated string. () → None."""
    if not ctx:
        return None
    return ".".join(str(s) for s in ctx)


def _extract_stream_index(display_name: str) -> int:
    """Extract numeric index from '[N]' display name. Returns 999999 on failure."""
    try:
        return int(display_name.strip("[]"))
    except (ValueError, AttributeError):
        return 999999


class TraceCollector:
    """Extracts trace data from graph (static) + state (dynamic).

    Graph metadata is precomputed once in __init__. Each collect(state)
    call does per-run state reading, context grouping, and tree building.

    Features:
        - Context grouping: streaming ops grouped under synthetic [N] nodes
        - Skip pending: generators with yield_count==0 removed by default
        - Flat trace for batch-only workflows (no synthetic nodes)

    Usage:
        collector = TraceCollector(graph)
        trace_data = collector.collect(state)
        trace_data = collector.collect(state, skip_pending=False)  # keep all
    """

    def __init__(self, graph: Any):
        """Precompute all graph-derived metadata.

        Args:
            graph: Root GraphOp (compiled workflow graph).
        """
        from hush.core.ops.graph.graph_op import GraphOp

        self._graph = graph

        # op_map: full_name → op object
        self._op_map: Dict[str, Any] = {}
        self._build_op_map(graph, self._op_map)

        # graph_meta: graph_full_name → {gen_ops, prevs, ops}
        self._graph_meta: Dict[str, Dict] = {}
        self._build_graph_meta(graph, self._graph_meta)

        # topo_rank: op_full_name → int (DAG ordering for sibling sorting)
        self._topo_ranks: Dict[str, int] = {}
        for _gname, meta in self._graph_meta.items():
            self._topo_ranks.update(compute_topo_rank(meta.get("ops", {}), meta.get("prevs", {})))

        # Per-op static info (doesn't change across runs)
        self._op_info: Dict[str, Dict[str, Any]] = {}
        for op_name, op in self._op_map.items():
            parent_name = op.parent.full_name if op.parent else None
            meta = self._graph_meta.get(parent_name, {})
            gen_ops = meta.get("gen_ops", set())
            is_root = parent_name is None
            short_name = op_name.rsplit(".", 1)[-1] if "." in op_name else op_name

            self._op_info[op_name] = {
                "op": op,
                "parent_name": parent_name,
                "is_root": is_root,
                "is_graph_op": isinstance(op, GraphOp) and not is_root,
                "is_gen": op_name in gen_ops,
                "short_name": short_name,
                "display_name": graph.name if is_root else short_name,
                "contain_generation": op.contain_generation,
                "gen_ops": gen_ops,
            }

    def collect(self, state: Any, skip_pending: bool = True) -> Dict[str, Any]:
        """Extract trace data as a pre-computed tree of TraceNodes.

        Args:
            state: MemoryState after execution completes.
            skip_pending: If True (default), remove generators with
                yield_count==0 and their empty context groups.

        Returns:
            Dict with request_id, workflow_name, nodes, summary.
        """
        # 1. Collect executed pairs from state
        executed_pairs: Set[Tuple] = set()
        for op_name in self._op_map:
            for ctx, _ in state.iter_executed(op_name):
                executed_pairs.add((op_name, ctx))

        # 2. Scan state → TraceNodes with parents resolved
        node_lookup, node_meta = self._scan_nodes(state, executed_pairs)

        # 3. Add synthetic context groups ([0], [1], ...)
        self._add_context_groups(node_lookup, node_meta)

        # 4. Skip pending generators and empty context groups
        if skip_pending:
            self._remove_pending(node_lookup)

        # 5. Sort by DAG edge order → final list
        children_map = build_children(
            list(node_lookup.keys()),
            lambda k: node_lookup[k].parent_trace_key,
        )
        result = self._sort_by_edges(node_lookup, children_map)

        # 6. Summary + payload
        summary = self._build_summary(result)
        return {
            "request_id": state.request_id,
            "user_id": state.user_id,
            "session_id": state.session_id,
            "workflow_name": self._graph.name,
            "tags": list(state.tags) if state.tags else [],
            "nodes": [asdict(n) for n in result],
            "summary": asdict(summary),
        }

    # =========================================================================
    # Graph Walking (init-time)
    # =========================================================================

    def _build_op_map(self, op: Any, result: Dict[str, Any]) -> None:
        """Recursively build full_name → op lookup from graph tree."""
        result[op.full_name] = op
        if hasattr(op, "_ops") and op._ops:
            for child in op._ops.values():
                self._build_op_map(child, result)

    def _build_graph_meta(self, graph: Any, result: Dict[str, Dict]) -> None:
        """Build per-graph metadata for kind/lineage derivation.

        Note: stream_contexts are NOT stored here — they're read live from
        the scheduler at collect() time via _get_stream_contexts().
        """
        from hush.core.ops.graph.graph_op import GraphOp

        gen_ops: Set[str] = set()
        for name, op in graph._ops.items():
            if _is_gen(op):
                gen_ops.add(op.full_name)
            if isinstance(op, GraphOp):
                self._build_graph_meta(op, result)

        result[graph.full_name] = {
            "gen_ops": gen_ops,
            "prevs": dict(graph.prevs),
            "ops": graph._ops,
        }

    # =========================================================================
    # Live state helpers
    # =========================================================================

    def _get_stream_contexts(self, parent_name: str) -> list:
        """Get stream_contexts from live scheduler (populated during run).

        Must read from the scheduler directly — not from init-time metadata —
        because Scheduler._reset() creates a new list each run().
        """
        parent_op = self._op_map.get(parent_name)
        if parent_op and hasattr(parent_op, "_scheduler") and parent_op._scheduler:
            return getattr(parent_op._scheduler, "stream_contexts", [])
        return []

    def _resolve_parent(
        self,
        parent_name: Optional[str],
        ctx: Tuple,
        executed_pairs: Set[Tuple],
    ) -> Tuple[Optional[str], Tuple]:
        """Resolve parent trace_key and the matched parent context.

        Walks up the context hierarchy to find the parent graph's
        execution context.

        Returns:
            (parent_trace_key, matched_parent_ctx)
        """
        if not parent_name:
            return None, ()
        if not ctx:
            return parent_name, ()
        if (parent_name, ctx) in executed_pairs:
            return f"{parent_name}:{_ctx_to_str(ctx)}", ctx
        test_ctx = ctx
        while test_ctx:
            if (parent_name, test_ctx) in executed_pairs:
                return f"{parent_name}:{_ctx_to_str(test_ctx)}", test_ctx
            test_ctx = test_ctx[:-1]
        return parent_name, ()

    # =========================================================================
    # Scan state → TraceNodes
    # =========================================================================

    def _scan_nodes(
        self,
        state: Any,
        executed_pairs: Set[Tuple],
    ) -> Tuple[Dict[str, TraceNode], Dict[str, Dict]]:
        """Walk state and build TraceNodes with parent_trace_key resolved.

        Returns:
            (node_lookup, node_meta) where node_meta maps trace_key to
            context info needed for grouping:
            {ctx, matched_parent_ctx, parent_graph_name}.
        """
        node_lookup: Dict[str, TraceNode] = {}
        node_meta: Dict[str, Dict] = {}

        for op_name, info in self._op_info.items():
            op = info["op"]
            parent_name = info["parent_name"]
            is_root = info["is_root"]
            is_graph_op = info["is_graph_op"]
            is_gen = info["is_gen"]
            display_name = info["display_name"]
            contain_generation = info["contain_generation"]

            # Get live stream_contexts from parent graph's scheduler
            stream_contexts = self._get_stream_contexts(parent_name) if parent_name else []

            for ctx, start_time in state.iter_executed(op_name):
                # Determine kind
                kind = self._determine_kind(ctx, is_gen, is_graph_op)

                # Read I/O (exclude internal $-prefixed keys)
                inputs = {
                    v: state[op_name, v, ctx] for v in (op.inputs or {}) if not v.startswith("$")
                }
                outputs = {
                    v: state[op_name, v, ctx] for v in (op.outputs or {}) if not v.startswith("$")
                }

                # Aggregate outputs for generators (collect from child contexts)
                if is_gen and all(v is None for v in outputs.values()):
                    agg = {v: [] for v in outputs}
                    for sc in stream_contexts:
                        if len(sc) == len(ctx) + 1 and sc[: len(ctx)] == ctx:
                            for v in agg:
                                val = state[op_name, v, sc]
                                if val is not None:
                                    agg[v].append(val)
                    if any(agg.values()):
                        outputs = agg

                # Timing
                end_time = state[op_name, "end_time", ctx]
                duration_ms = state[op_name, "duration_ms", ctx]

                # LLM-specific
                model = outputs.get("model_used") if contain_generation else None
                usage = outputs.get("tokens_used") if contain_generation else None
                cost = state[op_name, "cost_usd", ctx]

                # Trace key
                ctx_str = _ctx_to_str(ctx)
                trace_key = f"{op_name}:{ctx_str}" if ctx_str else op_name

                # node_type
                if is_root:
                    node_type = "trace"
                elif contain_generation:
                    node_type = "generation"
                else:
                    node_type = "span"

                # metadata
                metadata: Dict[str, Any] = {}
                if kind != "batch":
                    metadata["kind"] = kind
                if kind == "generator":
                    yield_count = self._count_yields(ctx, stream_contexts)
                    metadata["yield_count"] = yield_count
                    if yield_count == 0:
                        metadata["status"] = "pending"

                # Parent resolution
                if is_root:
                    parent_trace_key = None
                    matched_parent_ctx: Tuple = ()
                else:
                    parent_trace_key, matched_parent_ctx = self._resolve_parent(
                        parent_name, ctx, executed_pairs
                    )

                node = TraceNode(
                    trace_key=trace_key,
                    parent_trace_key=parent_trace_key,
                    op_name=op_name,
                    display_name=display_name,
                    node_type=node_type,
                    kind=kind,
                    inputs=inputs,
                    outputs=outputs,
                    start_time=self._format_time(start_time),
                    end_time=self._format_time(end_time),
                    duration_ms=duration_ms,
                    metadata=metadata,
                    model=model,
                    usage=usage,
                    cost=cost,
                )
                node_lookup[trace_key] = node
                node_meta[trace_key] = {
                    "ctx": ctx,
                    "matched_parent_ctx": matched_parent_ctx,
                    "parent_graph_name": parent_name,
                }

        return node_lookup, node_meta

    # =========================================================================
    # Context grouping
    # =========================================================================

    def _add_context_groups(
        self,
        node_lookup: Dict[str, TraceNode],
        node_meta: Dict[str, Dict],
    ) -> None:
        """Create synthetic [N] context nodes and re-parent streaming ops.

        Each synthetic [N] node is parented to the generator that spawned it.
        For example, if `audio` yields 5 items, contexts [0]-[4] become
        children of `audio`, not siblings.

        Example trace tree:
            callbot (trace)
            ├── audio (generator, yields=5)
            │   ├── [2] (stream_context)
            │   │   ├── v (generator)
            │   │   │   └── [0] (stream_context)
            │   │   │       ├── transcribe
            │   │   │       └── router (graph)
            │   │   └── ...
            │   └── [4] (stream_context)
            │       └── ...
        """
        # Build generator context map: (parent_graph, ctx) → trace_key
        gen_ctx_map: Dict[Tuple, str] = {}
        for trace_key, meta in node_meta.items():
            node = node_lookup.get(trace_key)
            if node and node.kind == "generator":
                gen_ctx_map[(meta["parent_graph_name"], meta["ctx"])] = trace_key

        synthetics: Dict[str, TraceNode] = {}

        for trace_key, meta in node_meta.items():
            node = node_lookup.get(trace_key)
            if not node or node.node_type == "trace":
                continue

            ctx = meta["ctx"]
            matched_parent_ctx = meta["matched_parent_ctx"]
            parent_graph_name = meta["parent_graph_name"]

            if not ctx or not matched_parent_ctx:
                continue

            # Compute relative segments beyond parent graph's context
            relative = ctx[len(matched_parent_ctx) :]
            if not relative:
                continue

            # Only group pure stream contexts ([N] segments only)
            # Mixed contexts (loops + streams) stay flat for now
            if not all(_is_stream_segment(s) for s in relative):
                continue

            # Build parent graph's trace_key (fallback if no spawning generator found)
            parent_ctx_str = _ctx_to_str(matched_parent_ctx)
            graph_trace_key = (
                f"{parent_graph_name}:{parent_ctx_str}" if parent_ctx_str else parent_graph_name
            )

            # Create synthetic nodes for each stream segment level
            deepest_synthetic = None
            for i in range(len(relative)):
                prefix = relative[: i + 1]
                full_ctx = matched_parent_ctx + prefix
                ctx_str = _ctx_to_str(full_ctx)
                synthetic_key = f"$ctx:{parent_graph_name}:{ctx_str}"

                if synthetic_key not in synthetics and synthetic_key not in node_lookup:
                    # Find the spawning generator for this level.
                    # The generator runs at: matched_parent_ctx + relative[:i]
                    gen_ctx = matched_parent_ctx + relative[:i]
                    spawner_key = gen_ctx_map.get((parent_graph_name, gen_ctx))
                    parent_for_synthetic = spawner_key if spawner_key else graph_trace_key

                    synthetics[synthetic_key] = TraceNode(
                        trace_key=synthetic_key,
                        parent_trace_key=parent_for_synthetic,
                        op_name=None,
                        display_name=relative[i],
                        node_type="span",
                        kind="stream_context",
                    )

                deepest_synthetic = synthetic_key

            # Re-parent this node to the deepest synthetic
            if deepest_synthetic:
                node.parent_trace_key = deepest_synthetic

        # Add synthetics to lookup and set timing from children
        node_lookup.update(synthetics)
        for key in synthetics:
            syn_node = node_lookup[key]
            children = [n for n in node_lookup.values() if n.parent_trace_key == key]
            starts = [c.start_time for c in children if c.start_time]
            ends = [c.end_time for c in children if c.end_time]
            if starts:
                syn_node.start_time = min(starts)
            if ends:
                syn_node.end_time = max(ends)

    def _remove_pending(self, node_lookup: Dict[str, TraceNode]) -> None:
        """Remove pending generators (yield_count==0) and empty context groups.

        Pending generators have metadata.status == "pending". After removing
        them, cascade-remove any synthetic context nodes left with no children.
        """
        # 1. Remove pending generators
        pending_keys = [k for k, n in node_lookup.items() if n.metadata.get("status") == "pending"]
        for k in pending_keys:
            del node_lookup[k]

        # 2. Cascade-remove empty synthetic context nodes
        changed = True
        while changed:
            changed = False
            parent_keys = {n.parent_trace_key for n in node_lookup.values()}
            empty = [
                k
                for k, n in node_lookup.items()
                if n.kind == "stream_context" and k not in parent_keys
            ]
            for k in empty:
                del node_lookup[k]
                changed = True

    # =========================================================================
    # Sort by DAG edge order
    # =========================================================================

    def _sort_by_edges(
        self,
        node_lookup: Dict[str, TraceNode],
        children_map: Dict[Optional[str], List[str]],
    ) -> List[TraceNode]:
        """Sort nodes by DAG topology, then DFS the tree.

        Ordering: real ops by topo_rank, then synthetic context nodes
        by stream index (so [0] < [1] < [2] < ... < [10]).
        """
        ranks = self._topo_ranks

        def _child_sort_key(k: str):
            n = node_lookup[k]
            if n.kind == "stream_context":
                # Synthetic context nodes sort after real ops, by stream index
                return (999998, _extract_stream_index(n.display_name), k)
            if n.op_name:
                return (ranks.get(n.op_name, 999999), 0, k)
            return (999999, 0, k)

        ordered_keys = tree_walk(
            children_map.get(None, []),
            children_map,
            sort_key=_child_sort_key,
        )
        return [node_lookup[k] for k in ordered_keys]

    # =========================================================================
    # Helpers
    # =========================================================================

    def _determine_kind(self, ctx: Tuple, is_gen: bool, is_graph_op: bool) -> str:
        """Determine record kind from context shape and op type."""
        if is_gen:
            return "generator"
        if is_graph_op:
            return "graph"
        if ctx:
            last_seg = ctx[-1]
            if _is_loop_segment(last_seg):
                return "loop_iter"
        return "batch"

    def _count_yields(self, gen_ctx: Tuple, stream_contexts: list) -> int:
        """Count how many stream contexts were spawned by a generator at gen_ctx."""
        prefix_len = len(gen_ctx)
        return sum(
            1 for sc in stream_contexts if len(sc) == prefix_len + 1 and sc[:prefix_len] == gen_ctx
        )

    def _format_time(self, t: Any) -> Optional[str]:
        """Format a time value to ISO string with 'Z' suffix for UTC."""
        if t is None:
            return None
        if isinstance(t, str):
            return t.replace("+00:00", "Z")
        iso = t.isoformat()
        iso = iso.replace("+00:00", "Z")
        if t.tzinfo is None and not iso.endswith("Z"):
            return iso + "Z"
        return iso

    def _build_summary(self, nodes: List[TraceNode]) -> TraceSummary:
        """Build summary from TraceNode list."""
        total_duration = 0.0
        total_yields = 0
        loop_iterations = 0
        stream_count = 0

        for n in nodes:
            if n.duration_ms:
                total_duration += n.duration_ms
            if n.kind == "generator":
                stream_count += 1
                total_yields += n.metadata.get("yield_count", 0)
            if n.kind == "loop_iter":
                loop_iterations += 1

        real_count = sum(1 for n in nodes if n.op_name is not None)
        return TraceSummary(
            total_ops=len(self._op_map),
            total_records=real_count,
            total_duration_ms=round(total_duration, 2),
            stream_count=stream_count,
            total_yields=total_yields,
            loop_iterations=loop_iterations,
            error_count=0,
        )
