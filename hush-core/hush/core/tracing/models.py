"""Data models for the tracing system."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    """

    op_name: str
    context_id: Optional[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    start_time: Optional[str] = None  # ISO format
    end_time: Optional[str] = None  # ISO format
    duration_ms: Optional[float] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    cost: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TracePayload:
    """Complete trace data for a single workflow execution.

    Combines static graph structure with dynamic execution records.
    Matches the IngestRequest format expected by hush-eyes server.
    """

    request_id: str
    workflow_name: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    graph_structure: List[NodeStructure] = field(default_factory=list)
    records: List[TraceRecord] = field(default_factory=list)
