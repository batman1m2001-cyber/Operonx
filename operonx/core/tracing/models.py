"""Data models for the tracing system."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from operonx.core.media import MediaRef


@dataclass
class TraceSummary:
    """Top-level summary of a workflow execution.

    Provides a quick overview without inspecting individual records.
    """

    total_ops: int = 0
    total_records: int = 0
    total_duration_ms: float = 0
    stream_count: int = 0  # number of generator ops
    total_yields: int = 0  # sum of all yield_counts
    loop_iterations: int = 0  # sum of all loop iterations
    error_count: int = 0


@dataclass
class TraceNode:
    """Pre-computed tree node for trace visualization.

    The collector builds these with parent_trace_key already resolved,
    so tracers (Langfuse, OTEL, etc.) do a simple parent lookup with
    zero heuristics.

    Kinds:
        - "batch": Normal op execution.
        - "generator": Generator summary (yield_count, total time).
        - "stream_context": Synthetic grouping span for execution slice [N].
        - "stream_item": Op execution inside a stream context.
        - "loop_iter": Synthetic grouping span for loop iteration [iter N].
        - "graph": Nested GraphOp container.
    """

    trace_key: str  # unique: "op_name:ctx_str" or synthetic "$ctx:main.s0"
    parent_trace_key: Optional[str]  # pre-computed — tracers just look this up

    op_name: Optional[str]  # None for synthetic nodes
    display_name: str  # short name for UI (e.g. "analyze", "[0]", "[iter 0]")
    node_type: str  # "trace" | "span" | "generation"
    kind: str  # "batch"|"generator"|"stream_context"|"stream_item"|"loop_iter"|"graph"

    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # LLM-specific (node_type="generation" only)
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    cost: Optional[float] = None

    # Extracted media blobs (images, audio, video). Populated by the collector
    # when normalize_trace_io surfaces Media instances. Tracers upload / drop
    # these separately from the main trace payload, then substitute back at
    # MediaRef.field_path.
    media: List[MediaRef] = field(default_factory=list)
