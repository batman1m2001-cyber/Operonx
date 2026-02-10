"""Trace reconstruction and dispatch for background flushing.

This module handles rebuilding flush_data from SQLite rows and dispatching
to the appropriate tracer backend (Langfuse, OTEL, etc.).
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional


def rebuild_flush_data(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    """Rebuild flush_data structure from flattened rows.

    Importantly, this function reorders nodes so that parents come before children,
    which is required for Langfuse hierarchy creation.
    """
    if not rows:
        return {}

    first_row = rows[0]
    flush_data = {
        "tracer_type": first_row["tracer_type"],
        "tracer_config": json.loads(first_row["tracer_config"])
        if first_row["tracer_config"]
        else {},
        "workflow_name": first_row["workflow_name"],
        "request_id": first_row["request_id"],
        "user_id": first_row["user_id"],
        "session_id": first_row["session_id"],
        "tags": json.loads(first_row["tags"]) if first_row["tags"] else [],
        "execution_order": [],
        "nodes_trace_data": {},
    }

    # Build node data first
    node_data_map = {}
    for row in rows:
        node_name = row["node_name"]
        context_id = row["context_id"]
        trace_key = f"{node_name}:{context_id}" if context_id else node_name

        # Skip duplicates (keep first occurrence)
        if trace_key in node_data_map:
            continue

        usage = None
        if row["prompt_tokens"] is not None or row["completion_tokens"] is not None:
            usage = {}
            if row["prompt_tokens"] is not None:
                usage["prompt_tokens"] = row["prompt_tokens"]
            if row["completion_tokens"] is not None:
                usage["completion_tokens"] = row["completion_tokens"]
            if row["total_tokens"] is not None:
                usage["total_tokens"] = row["total_tokens"]

        node_data_map[trace_key] = {
            "node": node_name,
            "parent": row["parent_name"],
            "context_id": context_id,
            "contain_generation": bool(row["contain_generation"]),
            "trace_data": {
                "name": node_name,
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "input": json.loads(row["input"]) if row["input"] else {},
                "output": json.loads(row["output"]) if row["output"] else {},
                "model": row["model"],
                "usage": usage,
                "cost": row["cost_usd"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            },
        }

    # Topological sort: parents before children
    def get_parent_key(
        node_name: str, parent_name: Optional[str], context_id: Optional[str]
    ) -> Optional[str]:
        """Get the key for the parent node."""
        if parent_name is None:
            return None
        parent_with_ctx = f"{parent_name}:{context_id}" if context_id else parent_name
        if parent_with_ctx in node_data_map:
            return parent_with_ctx
        if parent_name in node_data_map:
            return parent_name
        return parent_name

    ordered_keys = []
    visited = set()

    def visit(key: str):
        if key in visited:
            return
        visited.add(key)
        data = node_data_map.get(key)
        if data:
            parent_key = get_parent_key(data["node"], data["parent"], data["context_id"])
            if parent_key and parent_key in node_data_map:
                visit(parent_key)
        ordered_keys.append(key)

    for key in node_data_map:
        visit(key)

    # Build execution_order and nodes_trace_data in correct order
    for key in ordered_keys:
        data = node_data_map[key]
        flush_data["execution_order"].append(
            {
                "node": data["node"],
                "parent": data["parent"],
                "context_id": data["context_id"],
                "contain_generation": data["contain_generation"],
            }
        )
        flush_data["nodes_trace_data"][key] = data["trace_data"]

    return flush_data


def dispatch_flush(flush_data: Dict[str, Any]) -> None:
    """Dispatch flush to the appropriate tracer."""
    from hush.core.tracers.base import _TRACER_REGISTRY

    tracer_type = flush_data.get("tracer_type")

    if tracer_type not in _TRACER_REGISTRY:
        try:
            import hush.observability  # noqa: F401
        except ImportError:
            pass

    tracer_cls = _TRACER_REGISTRY.get(tracer_type)
    if tracer_cls is None:
        print(f"[BackgroundWorker] Unknown tracer type: {tracer_type}")
        return

    tracer_cls.flush(flush_data)
