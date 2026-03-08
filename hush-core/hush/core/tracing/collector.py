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
from hush.core.tracing.models import NodeStructure, TraceNode, TraceRecord, TraceSummary

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
        graph_meta: Dict[str, Dict],
        executed_pairs: Set[Tuple],
    ) -> Optional[str]:
        """Find which generator spawned a synthetic stream context.

        For ctx = ("main", "s0"), looks for generators in `owner` graph
        that executed at parent context ("main",).
        """
        meta = graph_meta.get(owner, {})
        gen_ops = meta.get("gen_ops", set())
        parent_ctx = ctx[:-1] if ctx else ()
        candidates = [g for g in gen_ops if (g, parent_ctx) in executed_pairs]
        if len(candidates) == 1:
            return candidates[0]
        elif candidates:
            return sorted(candidates)[0]
        return None

    def _format_time(self, t: Any) -> Optional[str]:
        """Format a time value to ISO string with 'Z' suffix for UTC.

        Langfuse requires UTC timestamps ending with 'Z', not '+00:00'.
        """
        if t is None:
            return None
        if isinstance(t, str):
            return t.replace("+00:00", "Z")
        iso = t.isoformat()
        # Normalize UTC offset to Z suffix
        iso = iso.replace("+00:00", "Z")
        # Append Z if naive datetime (no timezone info)
        if t.tzinfo is None and not iso.endswith("Z"):
            return iso + "Z"
        return iso

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
                    v: state[op_name, v, ctx] for v in (op.inputs or {}) if not v.startswith("$")
                }
                outputs = {
                    v: state[op_name, v, ctx] for v in (op.outputs or {}) if not v.startswith("$")
                }

                # For generators, aggregate outputs from stream contexts
                if kind == "generator" and all(v is None for v in outputs.values()):
                    agg_outputs = {v: [] for v in outputs}
                    for sc in stream_contexts:
                        if len(sc) == len(ctx) + 1 and sc[: len(ctx)] == ctx:
                            for v in agg_outputs:
                                val = state[op_name, v, sc]
                                if val is not None:
                                    agg_outputs[v].append(val)
                    if any(agg_outputs.values()):
                        outputs = agg_outputs

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
    # Tree-based Collection (new)
    # =========================================================================

    def collect_tree(self, graph: Any, state: Any) -> Dict[str, Any]:
        """Extract trace data as a pre-computed tree of TraceNodes.

        Returns a dict with ``nodes`` (List[dict]) where each node has
        ``parent_trace_key`` already resolved. Tracers do a simple parent
        lookup — zero heuristics.

        Args:
            graph: Root GraphOp (compiled workflow graph)
            state: MemoryState after execution completes

        Returns:
            Dict with request_id, workflow_name, nodes, summary.
        """
        from hush.core.ops.graph.graph_op import GraphOp

        # 1. Build lookups (reuse existing helpers)
        op_map: Dict[str, Any] = {}
        self._build_op_map(graph, op_map)
        graph_meta: Dict[str, Dict] = {}
        self._build_graph_meta(graph, graph_meta)

        # 2a. First scan — collect all executed (op_name, ctx) pairs
        executed_pairs: Set[Tuple] = set()
        for op_name in op_map:
            for ctx, _ in state.iter_executed(op_name):
                executed_pairs.add((op_name, ctx))

        # 2b. Second scan — build nodes, skipping yield records (Change B)
        raw_info: list = []
        yield_outputs: Dict[Tuple, Dict] = {}  # (op_name, ctx) → outputs

        for op_name, op in op_map.items():
            parent_name = op.parent.full_name if op.parent else None
            meta = graph_meta.get(parent_name, {})
            gen_ops = meta.get("gen_ops", set())
            stream_depths = meta.get("stream_depths", {})
            stream_contexts = meta.get("stream_contexts", [])
            prevs = meta.get("prevs", {})
            ops_in_graph = meta.get("ops", {})

            is_root = parent_name is None
            is_graph_op = isinstance(op, GraphOp) and not is_root
            is_gen = op_name in gen_ops

            for ctx, start_time in state.iter_executed(op_name):
                kind = self._determine_kind(op_name, ctx, gen_ops)

                # Change B: Skip generator yield records
                # A yield record = stream_item AND generator AND parent executed
                if kind == "stream_item" and is_gen:
                    parent_ctx = ctx[:-1] if ctx else ()
                    if (op_name, parent_ctx) in executed_pairs:
                        # Save outputs for potential context metadata
                        outputs = {
                            v: state[op_name, v, ctx]
                            for v in (op.outputs or {})
                            if not v.startswith("$")
                        }
                        yield_outputs[(op_name, ctx)] = outputs
                        continue

                # Read I/O (exclude internal $-prefixed keys)
                inputs = {
                    v: state[op_name, v, ctx] for v in (op.inputs or {}) if not v.startswith("$")
                }
                outputs = {
                    v: state[op_name, v, ctx] for v in (op.outputs or {}) if not v.startswith("$")
                }

                # Aggregate outputs for generators (use is_gen, not kind,
                # because kind may still be "stream_item" before Change D upgrade)
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
                model = outputs.get("model_used") if op.contain_generation else None
                usage = outputs.get("tokens_used") if op.contain_generation else None
                cost = state[op_name, "cost_usd", ctx]

                # --- Tree-specific fields ---
                ctx_str = _ctx_to_str(ctx)
                trace_key = f"{op_name}:{ctx_str}" if ctx_str else op_name

                # node_type
                if is_root:
                    node_type = "trace"
                elif op.contain_generation and kind != "stream_item":
                    node_type = "generation"
                else:
                    node_type = "span"

                # display_name
                short_name = op_name.rsplit(".", 1)[-1] if "." in op_name else op_name
                if is_root:
                    display_name = graph.name
                else:
                    display_name = short_name

                # Change D: Upgrade generators/GraphOps in stream contexts
                if kind == "stream_item" and is_gen:
                    # Generator executing in stream context (not yield — wasn't skipped)
                    node_kind = "generator"
                elif is_graph_op and kind in ("batch", "loop_iter", "stream_item"):
                    node_kind = "graph"
                elif kind == "loop_iter":
                    node_kind = "batch"  # real ops inside loops are just batch
                else:
                    node_kind = kind

                # metadata
                metadata: Dict[str, Any] = {}
                if kind not in ("batch",):
                    metadata["kind"] = kind
                if node_kind == "generator":
                    metadata["yield_count"] = self._count_yields(ctx, stream_contexts)
                if kind == "stream_item" and node_kind != "generator":
                    spawned_by = self._find_spawner(op.name, prevs, ops_in_graph, gen_ops)
                    if spawned_by:
                        metadata["spawned_by"] = spawned_by
                depth = stream_depths.get(op.name, 0)
                if depth > 0:
                    metadata["depth"] = depth

                node = TraceNode(
                    trace_key=trace_key,
                    parent_trace_key=None,  # resolved in pass 4
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

        # 3. Create synthetic context nodes for stream/loop groupings
        synthetic_contexts: Set[Tuple[str, Tuple]] = set()  # (owner_graph, ctx)
        for _node, parent_name, ctx in raw_info:
            if not ctx:
                continue
            last = ctx[-1]
            if not (_is_stream_segment(last) or _is_loop_segment(last)):
                continue
            owner = parent_name if parent_name else graph.full_name
            # Register this context + all ancestor contexts that are stream/loop
            for i in range(1, len(ctx) + 1):
                sub = ctx[:i]
                if _is_stream_segment(sub[-1]) or _is_loop_segment(sub[-1]):
                    synthetic_contexts.add((owner, sub))

        node_lookup: Dict[str, TraceNode] = {}

        # Build a map from graph op names to their trace_keys (for parent resolution)
        graph_trace_keys: Dict[str, str] = {}
        for node, _parent_name, _ctx in raw_info:
            if node.node_type == "trace" or node.kind == "graph":
                graph_trace_keys[node.op_name] = node.trace_key

        def _resolve_owner_trace_key(owner: str, ctx: Tuple) -> str:
            """Find the owning graph's trace_key by stripping stream/loop segments."""
            # If we have the owner in graph_trace_keys, use it directly
            if owner in graph_trace_keys:
                return graph_trace_keys[owner]
            # Fallback: compute from context (strip trailing stream/loop segments)
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
                idx = last[1:]  # "s0" → "0"
                # Change A: Name context after spawning generator
                spawner = self._find_context_spawner(owner, ctx, graph_meta, executed_pairs)
                spawner_short = spawner.rsplit(".", 1)[-1] if spawner else "ctx"
                display_name = f"{spawner_short}:{idx}"
                syn_kind = "stream_context"
            else:
                idx = last[5:]  # "loop_N" → N
                display_name = f"[iter {idx}]"
                syn_kind = "loop_iter"

            # Parent: up one level (if it's a registered synthetic) or the owning graph
            if len(ctx) == 1:
                syn_parent = _resolve_owner_trace_key(owner, ctx)
            else:
                parent_ctx = ctx[:-1]
                if (owner, parent_ctx) in synthetic_contexts:
                    parent_ctx_str = _ctx_to_str(parent_ctx)
                    syn_parent = f"$ctx:{owner}:{parent_ctx_str}"
                else:
                    # Parent sub-context isn't synthetic (e.g., "main"), use owning graph
                    syn_parent = _resolve_owner_trace_key(owner, ctx)

            syn_node = TraceNode(
                trace_key=ctx_key,
                parent_trace_key=syn_parent,
                op_name=None,
                display_name=display_name,
                node_type="span",
                kind=syn_kind,
            )
            node_lookup[ctx_key] = syn_node

        # 4. Resolve parent_trace_key for real records
        for node, parent_name, ctx in raw_info:
            if parent_name is None:
                # Root graph → becomes the trace
                node.parent_trace_key = None
            elif not ctx:
                # Default context → parent is the parent graph
                node.parent_trace_key = parent_name
            else:
                ctx_str = _ctx_to_str(ctx)
                # Check: did the parent graph also execute in this context?
                if (parent_name, ctx) in executed_pairs:
                    # Parent is a nested graph that ran in this context too
                    node.parent_trace_key = f"{parent_name}:{ctx_str}"
                else:
                    # Parent didn't run in this context → synthetic node
                    node.parent_trace_key = f"$ctx:{parent_name}:{ctx_str}"
            node_lookup[node.trace_key] = node

        # 5. Compute timing for synthetic nodes from their children
        for node, _parent_name, _ctx in raw_info:
            pk = node.parent_trace_key
            if pk and pk in node_lookup:
                syn = node_lookup[pk]
                if syn.op_name is None:  # synthetic only
                    if node.start_time:
                        if syn.start_time is None or node.start_time < syn.start_time:
                            syn.start_time = node.start_time
                    if node.end_time:
                        if syn.end_time is None or node.end_time > syn.end_time:
                            syn.end_time = node.end_time

        # 6. Change C: Drop zero-yield generators in stream contexts
        to_drop: Set[str] = set()
        for key, node in node_lookup.items():
            if (
                node.kind == "generator"
                and node.metadata.get("yield_count", -1) == 0
                and node.parent_trace_key
                and node.parent_trace_key.startswith("$ctx:")
            ):
                to_drop.add(key)
        for key in to_drop:
            del node_lookup[key]

        # 7. Change E: Single-child collapse
        #    If a stream_context has exactly 1 child, merge child up
        children_of: Dict[str, List[str]] = defaultdict(list)
        for key, node in node_lookup.items():
            if node.parent_trace_key and node.parent_trace_key in node_lookup:
                children_of[node.parent_trace_key].append(key)

        to_remove: Set[str] = set()
        for ctx_key, children in children_of.items():
            ctx_node = node_lookup.get(ctx_key)
            if not ctx_node or ctx_node.kind != "stream_context" or len(children) != 1:
                continue
            child = node_lookup[children[0]]
            # Extract index from "gen:N" → N
            parts = ctx_node.display_name.rsplit(":", 1)
            idx = parts[1] if len(parts) == 2 else "0"
            child.display_name = f"{child.display_name}[{idx}]"
            child.parent_trace_key = ctx_node.parent_trace_key
            if ctx_node.metadata.get("yield_output"):
                child.metadata["yield_output"] = ctx_node.metadata["yield_output"]
            to_remove.add(ctx_key)

        for key in to_remove:
            del node_lookup[key]

        # 8. Change F: Flatten single-yield inner contexts
        #    If a generator inside a stream_context has yield_count == 1,
        #    promote the inner context's children up and remove it
        children_of = defaultdict(list)
        for key, node in node_lookup.items():
            if node.parent_trace_key and node.parent_trace_key in node_lookup:
                children_of[node.parent_trace_key].append(key)

        to_remove = set()
        for ctx_key, children in children_of.items():
            ctx_node = node_lookup.get(ctx_key)
            if not ctx_node or ctx_node.kind != "stream_context":
                continue
            for child_key in children:
                child = node_lookup[child_key]
                if child.kind == "generator" and child.metadata.get("yield_count") == 1:
                    # Find the single inner context among siblings
                    inner_ctx = None
                    for sibling_key in children:
                        sibling = node_lookup[sibling_key]
                        if sibling.kind == "stream_context":
                            inner_ctx = sibling
                            break
                    if inner_ctx and inner_ctx.trace_key in children_of:
                        # Promote inner context's children to outer context
                        for grandchild_key in children_of[inner_ctx.trace_key]:
                            node_lookup[grandchild_key].parent_trace_key = ctx_key
                        to_remove.add(inner_ctx.trace_key)

        for key in to_remove:
            del node_lookup[key]

        # 8b. Prune empty synthetic nodes (no children after all transforms)
        #     Iterate until stable — removing a child may empty its parent
        changed = True
        while changed:
            changed = False
            children_of = defaultdict(list)
            for key, node in node_lookup.items():
                if node.parent_trace_key and node.parent_trace_key in node_lookup:
                    children_of[node.parent_trace_key].append(key)
            to_remove = set()
            for key, node in node_lookup.items():
                if (
                    node.op_name is None
                    and node.kind in ("stream_context", "loop_iter")
                    and key not in children_of
                ):
                    to_remove.add(key)
            for key in to_remove:
                del node_lookup[key]
                changed = True

        # 9. Topological sort — parents before children
        result: List[TraceNode] = []
        visited: Set[str] = set()

        def _visit(key: str) -> None:
            if key in visited or key not in node_lookup:
                return
            visited.add(key)
            n = node_lookup[key]
            if n.parent_trace_key and n.parent_trace_key in node_lookup:
                _visit(n.parent_trace_key)
            result.append(n)

        # Sort keys for deterministic sibling order (by start_time, then key)
        sorted_keys = sorted(
            node_lookup.keys(),
            key=lambda k: (node_lookup[k].start_time or "", k),
        )
        for key in sorted_keys:
            _visit(key)

        # 10. Build summary from real nodes
        summary = self._build_tree_summary(op_map, result)

        return {
            "request_id": state.request_id,
            "user_id": state.user_id,
            "session_id": state.session_id,
            "workflow_name": graph.name,
            "tags": list(state.tags) if state.tags else [],
            "nodes": [asdict(n) for n in result],
            "summary": asdict(summary),
        }

    def _build_tree_summary(self, op_map: Dict[str, Any], nodes: List[TraceNode]) -> TraceSummary:
        """Build summary from TraceNode list."""
        total_duration = 0.0
        total_yields = 0
        loop_iterations = 0
        stream_count = 0

        for n in nodes:
            if n.op_name is None:
                # Synthetic nodes don't count toward op metrics
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
            total_ops=len(op_map),
            total_records=real_count,
            total_duration_ms=round(total_duration, 2),
            stream_count=stream_count,
            total_yields=total_yields,
            loop_iterations=loop_iterations,
            error_count=0,
        )

    # =========================================================================
    # Summary
    # =========================================================================

    def _build_summary(self, op_map: Dict[str, Any], records: List[TraceRecord]) -> TraceSummary:
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
