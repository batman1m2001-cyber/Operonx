"""Data models for the tracing system."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class NodeStructure:
    """Static metadata — captured once from compiled graph.

    Represents the structure of a single op in the workflow graph.
    This data comes from op @properties and doesn't change between executions.
    """

    op_name: str
    op_type: str
    parent_name: Optional[str]
    contain_generation: bool


@dataclass
class TraceRecord:
    """Dynamic data — read from state after execution.

    Represents a single op execution with its runtime data.
    All values are read from MemoryState cells.

    Kinds:
        - "batch": Normal op, runs once per context.
        - "generator": Generator op, consumed inputs once, produced M yields.
        - "stream_item": One yield from a generator, stored in a stream context.
        - "loop_iter": One iteration of a GraphOp.loop() execution.
    """

    op_name: str
    context: Optional[Tuple] = None
    kind: str = "batch"  # "batch" | "generator" | "stream_item" | "loop_iter"
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    start_time: Optional[str] = None  # ISO format
    end_time: Optional[str] = None  # ISO format
    duration_ms: Optional[float] = None

    # Streaming (kind="generator" only)
    yield_count: Optional[int] = None

    # Context lineage
    depth: int = 0  # nesting level in graph hierarchy
    parent_context: Optional[Tuple] = None  # ("main",) for ("main","s0")
    spawned_by: Optional[str] = None  # op_name of generator that created this context

    # LLM fields
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    cost: Optional[float] = None


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


@dataclass
class TracePayload:
    """Complete trace data for a single workflow execution.

    Combines static graph structure with dynamic execution records.
    Matches the IngestRequest format expected by ui-hush-eyes server.
    """

    request_id: str
    workflow_name: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    graph_structure: List[NodeStructure] = field(default_factory=list)
    records: List[TraceRecord] = field(default_factory=list)
    summary: Optional[TraceSummary] = None
