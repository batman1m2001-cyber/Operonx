"""TraceCollector — pure post-processing step for extracting trace data.

The collector is completely separated from ops. Ops don't know about tracing.
After workflow execution, the collector walks the compiled graph for static
metadata and reads dynamic execution data from state.

Streaming-aware: derives kind, context lineage, yield counts, and spawned_by
from existing graph/scheduler data. Zero changes to core code required.

Usage:
    collector = TraceCollector(graph)   # precomputes graph metadata + topo order
    trace_data = collector.collect(state)  # per-run: reads state, builds tree
"""

import logging
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from hush.core.ops.graph.scheduler import _is_gen
from hush.core.tracing.models import TraceNode, TraceSummary
from hush.core.utils.algo import build_children, collapse, prune_leaves, tree_walk
from hush.core.utils.algo import topo_rank as compute_topo_rank

LOGGER = logging.getLogger("hush.tracing")


def _is_stream_segment(seg: str) -> bool:
    """Check if a context segment is a stream segment like 's0', 's12'."""
    return isinstance(seg, str) and len(seg) > 1 and seg[0] == "s" and seg[1:].isdigit()


def _is_loop_segment(seg: str) -> bool:
    """Check if a context segment is a loop iteration like 'loop_0', 'loop_5'."""
    return isinstance(seg, str) and seg.startswith("loop_")


def _ctx_to_str(ctx: Tuple) -> Optional[str]:
    """Convert a context tuple to dot-separated string. () → None."""
    if not ctx:
        return None
    return ".".join(str(s) for s in ctx)


class TraceCollector:
    """Extracts trace data from graph (static) + state (dynamic).

    Graph metadata is precomputed once in __init__. Each collect(state)
    call only does the per-run state reading and tree building.

    Usage:
        collector = TraceCollector(graph)
        trace_data = collector.collect(state)
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

        # graph_meta: graph_full_name → {gen_ops, stream_depths, stream_contexts, prevs, ops}
        self._graph_meta: Dict[str, Dict] = {}
        self._build_graph_meta(graph, self._graph_meta)

        # topo_rank: op_full_name → int (DAG ordering for sibling sorting)
        self._topo_ranks: Dict[str, int] = {}
        for _gname, meta in self._graph_meta.items():
            self._topo_ranks.update(
                compute_topo_rank(meta.get("ops", {}), meta.get("prevs", {}))
            )

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
                "stream_depths": meta.get("stream_depths", {}),
                "stream_contexts": meta.get("stream_contexts", []),
                "prevs": meta.get("prevs", {}),
                "ops_in_graph": meta.get("ops", {}),
            }

            # Precompute spawned_by (BFS through predecessors to find generator)
            if not is_root:
                self._op_info[op_name]["spawned_by"] = self._find_spawner(
                    op.name, meta.get("prevs", {}), meta.get("ops", {}), gen_ops
                )

    def collect(self, state: Any) -> Dict[str, Any]:
        """Extract trace data as a pre-computed tree of TraceNodes.

        Returns a dict with ``nodes`` (List[dict]) where each node has
        ``parent_trace_key`` already resolved. Tracers do a simple parent
        lookup — zero heuristics.

        Args:
            state: MemoryState after execution completes.

        Returns:
            Dict with request_id, workflow_name, nodes, summary.
        """
        # 1. Collect executed pairs from state
        executed_pairs: Set[Tuple] = set()
        for op_name in self._op_map:
            for ctx, _ in state.iter_executed(op_name):
                executed_pairs.add((op_name, ctx))

        # 2. Scan state → raw TraceNodes
        raw_info = self._scan_nodes(state, executed_pairs)

        # 3. Create synthetic context nodes + resolve parents
        node_lookup = self._create_synthetics(raw_info, executed_pairs)
        self._resolve_parents(raw_info, node_lookup, executed_pairs)

        # 4. Tree transforms (drop, collapse, flatten, prune)
        children_map = self._transform_tree(node_lookup)

        # 5. Sort by DAG edge order → final list
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
        """Build per-graph streaming metadata for kind/lineage derivation."""
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
    # Step 2: Scan state → raw TraceNodes
    # =========================================================================

    def _scan_nodes(
        self,
        state: Any,
        executed_pairs: Set[Tuple],
    ) -> list:
        """Walk state and build raw_info list of (TraceNode, parent_name, ctx).

        Applies Change B (skip generator yield records) and Change D
        (upgrade generators/GraphOps in stream contexts).
        """
        raw_info: list = []

        for op_name, info in self._op_info.items():
            op = info["op"]
            parent_name = info["parent_name"]
            is_root = info["is_root"]
            is_graph_op = info["is_graph_op"]
            is_gen = info["is_gen"]
            gen_ops = info["gen_ops"]
            stream_contexts = info["stream_contexts"]
            stream_depths = info["stream_depths"]
            display_name = info["display_name"]
            contain_generation = info["contain_generation"]

            for ctx, start_time in state.iter_executed(op_name):
                kind = self._determine_kind(op_name, ctx, gen_ops)

                # Change B: Skip generator yield records
                if kind == "stream_item" and is_gen:
                    parent_ctx = ctx[:-1] if ctx else ()
                    if (op_name, parent_ctx) in executed_pairs:
                        continue

                # Read I/O (exclude internal $-prefixed keys)
                inputs = {
                    v: state[op_name, v, ctx] for v in (op.inputs or {}) if not v.startswith("$")
                }
                outputs = {
                    v: state[op_name, v, ctx] for v in (op.outputs or {}) if not v.startswith("$")
                }

                # Aggregate outputs for generators
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

                # Tree-specific fields
                ctx_str = _ctx_to_str(ctx)
                trace_key = f"{op_name}:{ctx_str}" if ctx_str else op_name

                # node_type
                if is_root:
                    node_type = "trace"
                elif contain_generation and kind != "stream_item":
                    node_type = "generation"
                else:
                    node_type = "span"

                # Change D: Upgrade generators/GraphOps in stream contexts
                if kind == "stream_item" and is_gen:
                    node_kind = "generator"
                elif is_graph_op and kind in ("batch", "loop_iter", "stream_item"):
                    node_kind = "graph"
                elif kind == "loop_iter":
                    node_kind = "batch"
                else:
                    node_kind = kind

                # metadata
                metadata: Dict[str, Any] = {}
                if kind not in ("batch",):
                    metadata["kind"] = kind
                if node_kind == "generator":
                    metadata["yield_count"] = self._count_yields(ctx, stream_contexts)
                if kind == "stream_item" and node_kind != "generator":
                    spawned_by = info.get("spawned_by")
                    if spawned_by:
                        metadata["spawned_by"] = spawned_by
                depth = stream_depths.get(op.name, 0)
                if depth > 0:
                    metadata["depth"] = depth

                node = TraceNode(
                    trace_key=trace_key,
                    parent_trace_key=None,
                    op_name=op_name,
                    display_name=display_name,
                    node_type=node_type,
                    kind=node_kind,
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
                raw_info.append((node, parent_name, ctx))

        return raw_info

    # =========================================================================
    # Step 3: Create synthetic context nodes
    # =========================================================================

    def _create_synthetics(
        self,
        raw_info: list,
        executed_pairs: Set[Tuple],
    ) -> Dict[str, TraceNode]:
        """Create $ctx: synthetic grouping nodes for stream/loop contexts.

        Returns node_lookup populated with synthetic nodes only.
        """
        graph = self._graph
        graph_meta = self._graph_meta

        synthetic_contexts: Set[Tuple[str, Tuple]] = set()
        for _node, parent_name, ctx in raw_info:
            if not ctx:
                continue
            last = ctx[-1]
            if not (_is_stream_segment(last) or _is_loop_segment(last)):
                continue
            owner = parent_name if parent_name else graph.full_name
            for i in range(1, len(ctx) + 1):
                sub = ctx[:i]
                if _is_stream_segment(sub[-1]) or _is_loop_segment(sub[-1]):
                    synthetic_contexts.add((owner, sub))

        node_lookup: Dict[str, TraceNode] = {}

        # Map graph op names → trace_keys for parent resolution
        graph_trace_keys: Dict[str, str] = {}
        for node, _parent_name, _ctx in raw_info:
            if node.node_type == "trace" or node.kind == "graph":
                graph_trace_keys[node.op_name] = node.trace_key

        def _resolve_owner_trace_key(owner: str, ctx: Tuple) -> str:
            if owner in graph_trace_keys:
                return graph_trace_keys[owner]
            owner_ctx = ctx
            while owner_ctx and (
                _is_stream_segment(owner_ctx[-1]) or _is_loop_segment(owner_ctx[-1])
            ):
                owner_ctx = owner_ctx[:-1]
            owner_ctx_str = _ctx_to_str(owner_ctx)
            return f"{owner}:{owner_ctx_str}" if owner_ctx_str else owner

        for owner, ctx in sorted(synthetic_contexts, key=lambda x: (x[0], len(x[1]), x[1])):
            ctx_str = _ctx_to_str(ctx)
            ctx_key = f"$ctx:{owner}:{ctx_str}"
            last = ctx[-1]

            if _is_stream_segment(last):
                idx = last[1:]
                spawner = self._find_context_spawner(owner, ctx, executed_pairs)
                spawner_short = spawner.rsplit(".", 1)[-1] if spawner else "ctx"
                display_name = f"{spawner_short}:{idx}"
                syn_kind = "stream_context"
            else:
                idx = last[5:]
                display_name = f"[iter {idx}]"
                syn_kind = "loop_iter"

            if len(ctx) == 1:
                syn_parent = _resolve_owner_trace_key(owner, ctx)
            else:
                parent_ctx = ctx[:-1]
                if (owner, parent_ctx) in synthetic_contexts:
                    parent_ctx_str = _ctx_to_str(parent_ctx)
                    syn_parent = f"$ctx:{owner}:{parent_ctx_str}"
                else:
                    syn_parent = _resolve_owner_trace_key(owner, ctx)

            node_lookup[ctx_key] = TraceNode(
                trace_key=ctx_key,
                parent_trace_key=syn_parent,
                op_name=None,
                display_name=display_name,
                node_type="span",
                kind=syn_kind,
            )

        return node_lookup

    # =========================================================================
    # Step 4: Resolve parents + timing
    # =========================================================================

    def _resolve_parents(
        self,
        raw_info: list,
        node_lookup: Dict[str, TraceNode],
        executed_pairs: Set[Tuple],
    ) -> None:
        """Set parent_trace_key for real nodes, compute synthetic timing."""
        for node, parent_name, ctx in raw_info:
            if parent_name is None:
                node.parent_trace_key = None
            elif not ctx:
                node.parent_trace_key = parent_name
            else:
                ctx_str = _ctx_to_str(ctx)
                if (parent_name, ctx) in executed_pairs:
                    node.parent_trace_key = f"{parent_name}:{ctx_str}"
                else:
                    node.parent_trace_key = f"$ctx:{parent_name}:{ctx_str}"
            node_lookup[node.trace_key] = node

        # Compute timing for synthetic nodes from their children
        for node, _parent_name, _ctx in raw_info:
            pk = node.parent_trace_key
            if pk and pk in node_lookup:
                syn = node_lookup[pk]
                if syn.op_name is None:
                    if node.start_time:
                        if syn.start_time is None or node.start_time < syn.start_time:
                            syn.start_time = node.start_time
                    if node.end_time:
                        if syn.end_time is None or node.end_time > syn.end_time:
                            syn.end_time = node.end_time

    # =========================================================================
    # Step 5: Tree transforms
    # =========================================================================

    def _transform_tree(
        self,
        node_lookup: Dict[str, TraceNode],
    ) -> Dict[Optional[str], List[str]]:
        """Apply Changes C, E, F and prune empty synthetics.

        Returns the children_map for sorting.
        """

        # Change C: Drop zero-yield generators in stream contexts
        to_drop = [
            key
            for key, node in node_lookup.items()
            if node.kind == "generator"
            and node.metadata.get("yield_count", -1) == 0
            and node.parent_trace_key
            and node.parent_trace_key.startswith("$ctx:")
        ]
        for key in to_drop:
            del node_lookup[key]

        # Build children_map once, reuse for all transforms
        children_map = build_children(
            list(node_lookup.keys()),
            lambda k: node_lookup[k].parent_trace_key,
        )

        # Change E: Single-child collapse — stream_context with 1 child
        def _on_collapse_ctx(ctx_key: str, child_key: str) -> None:
            ctx_node = node_lookup[ctx_key]
            child = node_lookup[child_key]
            parts = ctx_node.display_name.rsplit(":", 1)
            idx = parts[1] if len(parts) == 2 else "0"
            child.display_name = f"{child.display_name}[{idx}]"
            child.parent_trace_key = ctx_node.parent_trace_key
            if ctx_node.metadata.get("yield_output"):
                child.metadata["yield_output"] = ctx_node.metadata["yield_output"]

        collapsed = collapse(
            children_map,
            predicate=lambda k: k in node_lookup and node_lookup[k].kind == "stream_context",
            on_collapse=_on_collapse_ctx,
        )
        for key in collapsed:
            del node_lookup[key]

        # Change F: Flatten single-yield inner contexts
        to_remove: Set[str] = set()
        for ctx_key in list(children_map.keys()):
            if ctx_key is None or ctx_key not in node_lookup:
                continue
            ctx_node = node_lookup[ctx_key]
            if ctx_node.kind != "stream_context":
                continue
            kids = children_map.get(ctx_key, [])
            has_single_yield_gen = any(
                node_lookup[k].kind == "generator"
                and node_lookup[k].metadata.get("yield_count") == 1
                for k in kids
                if k in node_lookup
            )
            if not has_single_yield_gen:
                continue
            inner_ctx_key = next(
                (k for k in kids if k in node_lookup and node_lookup[k].kind == "stream_context"),
                None,
            )
            if inner_ctx_key and inner_ctx_key in children_map:
                for grandchild_key in children_map[inner_ctx_key]:
                    node_lookup[grandchild_key].parent_trace_key = ctx_key
                children_map[ctx_key] = [
                    k for k in kids if k != inner_ctx_key
                ] + children_map[inner_ctx_key]
                del children_map[inner_ctx_key]
                to_remove.add(inner_ctx_key)
        for key in to_remove:
            del node_lookup[key]

        # Prune empty synthetic nodes (recursive leaf removal)
        pruned = prune_leaves(
            children_map,
            predicate=lambda k: (
                k in node_lookup
                and node_lookup[k].op_name is None
                and node_lookup[k].kind in ("stream_context", "loop_iter")
            ),
        )
        for key in pruned:
            node_lookup.pop(key, None)

        return children_map

    # =========================================================================
    # Step 6: Sort by DAG edge order
    # =========================================================================

    def _sort_by_edges(
        self,
        node_lookup: Dict[str, TraceNode],
        children_map: Dict[Optional[str], List[str]],
    ) -> List[TraceNode]:
        """Sort nodes by DAG topology, then DFS the tree."""
        ranks = self._topo_ranks

        def _child_sort_key(k: str):
            n = node_lookup[k]
            if n.op_name:
                return (ranks.get(n.op_name, 999999), 0, k)
            # Synthetic nodes sort after the generator/loop op that spawned them
            if n.kind in ("stream_context", "loop_iter") and ":" in n.display_name:
                spawner_short = n.display_name.rsplit(":", 1)[0]
                for sib_key in children_map.get(n.parent_trace_key, []):
                    sib = node_lookup[sib_key]
                    if sib.op_name and sib.op_name.endswith("." + spawner_short):
                        return (ranks.get(sib.op_name, 999999), 1, k)
            return (999999, 1, k)

        ordered_keys = tree_walk(
            children_map.get(None, []),
            children_map,
            sort_key=_child_sort_key,
        )
        return [node_lookup[k] for k in ordered_keys]

    # =========================================================================
    # Helpers
    # =========================================================================

    def _determine_kind(self, op_full_name: str, ctx: Tuple, gen_ops: Set[str]) -> str:
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

    def _count_yields(self, gen_ctx: Tuple, stream_contexts: List[Tuple]) -> int:
        """Count how many stream contexts were spawned by a generator at gen_ctx."""
        prefix_len = len(gen_ctx)
        return sum(
            1 for sc in stream_contexts if len(sc) == prefix_len + 1 and sc[:prefix_len] == gen_ctx
        )

    def _find_context_spawner(
        self,
        owner: str,
        ctx: Tuple,
        executed_pairs: Set[Tuple],
    ) -> Optional[str]:
        """Find which generator spawned a synthetic stream context."""
        meta = self._graph_meta.get(owner, {})
        gen_ops = meta.get("gen_ops", set())
        parent_ctx = ctx[:-1] if ctx else ()
        candidates = [g for g in gen_ops if (g, parent_ctx) in executed_pairs]
        if len(candidates) == 1:
            return candidates[0]
        elif candidates:
            return sorted(candidates)[0]
        return None

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
            if n.op_name is None:
                if n.kind == "loop_iter":
                    loop_iterations += 1
                continue
            if n.duration_ms:
                total_duration += n.duration_ms
            if n.kind == "generator":
                stream_count += 1
                total_yields += n.metadata.get("yield_count", 0)

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
