"""TraceCollector — pure post-processing step for extracting trace data.

The collector is completely separated from ops. Ops don't know about tracing.
After workflow execution, the collector walks the compiled graph for static
metadata and reads dynamic execution data from state.
"""

import logging
from dataclasses import asdict
from typing import Any, Dict, List

from hush.core.tracing.models import NodeStructure, TraceRecord

LOGGER = logging.getLogger("hush.tracing")


class TraceCollector:
    """Extracts trace data from graph (static) + state (dynamic).

    Static data: op_type, parent_name, contain_generation — from op @properties.
    Dynamic data: inputs, outputs, timing, model, usage, cost — from state cells.

    Usage:
        collector = TraceCollector()
        trace_data = collector.collect(graph, state)
        # trace_data matches hush-eyes IngestRequest format
    """

    def collect(self, graph: Any, state: Any) -> Dict[str, Any]:
        """Extract all trace data after workflow execution.

        Args:
            graph: Root GraphOp (compiled workflow graph)
            state: MemoryState after execution completes

        Returns:
            Dict matching hush-eyes IngestRequest format:
            {request_id, workflow_name, user_id, session_id, tags,
             graph_structure, records}
        """
        # 1. Build op lookup: full_name → op object
        op_map: Dict[str, Any] = {}
        self._build_op_map(graph, op_map)

        # 2. Static: graph structure from op @properties
        graph_structure = self._collect_graph_structure(op_map)

        # 3. Dynamic: walk state.execution_order, read values from state
        records = self._collect_records(op_map, state)

        # 4. Build payload — tags here are dynamic only (from $tags in op outputs)
        # Static tracer tags are merged later by FlushWorker per-tracer
        return {
            "request_id": state.request_id,
            "user_id": state.user_id,
            "session_id": state.session_id,
            "workflow_name": graph.name,
            "tags": list(state.tags) if state.tags else [],
            "graph_structure": [asdict(n) for n in graph_structure],
            "records": [asdict(r) for r in records],
        }

    def _build_op_map(self, op: Any, result: Dict[str, Any]) -> None:
        """Recursively build full_name → op lookup from graph tree."""
        result[op.full_name] = op
        if hasattr(op, "_ops") and op._ops:
            for child in op._ops.values():
                self._build_op_map(child, result)

    def _collect_graph_structure(self, op_map: Dict[str, Any]) -> List[NodeStructure]:
        """Extract static metadata from all ops in the graph."""
        return [
            NodeStructure(
                op_name=op.full_name,
                op_type=getattr(op, "type", "default"),
                parent_name=op.father.full_name if op.father else None,
                contain_generation=op.contain_generation,
            )
            for op in op_map.values()
        ]

    def _collect_records(self, op_map: Dict[str, Any], state: Any) -> List[TraceRecord]:
        """Extract dynamic execution data from state, in execution order."""
        records = []

        for entry in state.execution_order:
            op_name = entry["op"]
            ctx = entry.get("context_id")
            op = op_map.get(op_name)
            if not op:
                continue

            # Read I/O from state
            inputs = {v: state[op_name, v, ctx] for v in (op.inputs or {})}
            outputs = {v: state[op_name, v, ctx] for v in (op.outputs or {})}

            # Read timing from state
            start_time = state[op_name, "start_time", ctx]
            end_time = state[op_name, "end_time", ctx]
            duration_ms = state[op_name, "duration_ms", ctx]

            # LLM-specific: model & usage are in outputs, cost in state
            model = outputs.get("model_used") if op.contain_generation else None
            usage = outputs.get("tokens_used") if op.contain_generation else None
            cost = state[op_name, "cost_usd", ctx]

            records.append(
                TraceRecord(
                    op_name=op_name,
                    context_id=ctx,
                    inputs=inputs,
                    outputs=outputs,
                    start_time=start_time.isoformat() if start_time else None,
                    end_time=end_time.isoformat() if end_time else None,
                    duration_ms=duration_ms,
                    model=model,
                    usage=usage,
                    cost=cost,
                )
            )

        return records
