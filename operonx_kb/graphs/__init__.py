"""Graphs: wiring only."""

from operonx_kb.graphs.ingest import build_ingest_flow, build_ingest_graph
from operonx_kb.graphs.maintenance import build_delete_graph, build_gc_graph

__all__ = ["build_delete_graph", "build_gc_graph", "build_ingest_flow", "build_ingest_graph"]
