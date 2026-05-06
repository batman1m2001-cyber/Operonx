"""TraceEvent — atomic record of one thing that happened during workflow execution.

Flat, immutable, no parent pointers. Tree/timeline/grouped shapes are
reconstructed by exporters from `(op_name, ctx)` tuples and `GROUP_START`/
`GROUP_END` markers in the stream.

Part of the new event-stream tracing pipeline. Lives alongside the legacy
`TraceCollector` during phases T1–T2; replaces it at T2.

See ``docs/TRACING_REDESIGN_PLAN.md`` §3.1.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EventKind(str, Enum):
    """The kinds of events ops + scheduler can emit.

    String values so events serialize cleanly to JSON for cross-runtime parity.
    """

    OP_START = "op_start"
    """Fired before op body runs. Payload: ``{inputs: dict}``."""

    OP_END = "op_end"
    """Fired after op finishes (any reason).
    Payload: ``{outputs: dict, status: str, duration_ms: float, yield_count: int}``.
    Status is one of: ``"ok"``, ``"error"``, ``"cancelled"``.
    """

    OP_YIELD = "op_yield"
    """Fired per-yield from a generator op (gated by ``@op(emit_yields=N)``).
    Payload: ``{yielded: dict, idx: int}``. ``idx`` is the absolute yield index.
    """

    ANNOTATION = "annotation"
    """User-attached metadata, scoped to the currently executing ``(op, ctx)``
    via ``_current_op_var``. Payload: ``{key: str, value: Any}``.
    """

    GROUP_START = "group_start"
    """Synthetic — emitted by ``GroupBy`` processor at boundary detection,
    or by ``EventEmitter.group()`` context manager. Payload: ``{name: str, ...metadata}``.
    """

    GROUP_END = "group_end"
    """Closes a ``GROUP_START``. Payload: ``{name: str, status: str}``.
    Status is one of: ``"ok"``, ``"truncated"`` (pipeline shutdown closed
    open groups).
    """

    LLM_USAGE = "llm_usage"
    """Token / cost report from an LLM op. MUST be emitted before the
    matching ``OP_END`` for the same op.
    Payload: ``{model: str, prompt_tokens: int, completion_tokens: int,
    total_tokens: int, cost_usd: float}``.
    """

    MEDIA_REF = "media_ref"
    """Reference to a binary blob stored in the pipeline's MediaStore.
    Bytes live in the store, not the event. Payload: ``{handle: str,
    mime: str, size_bytes: int}``.
    """


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One atomic record of what happened.

    Immutable. Construction is the only mutation. Sortable by
    ``(timestamp, seq)`` — ``seq`` breaks ties when many events land in the
    same microsecond.

    The ``ctx`` tuple matches the scheduler's context tuple semantics:
    ``("main",)`` is the root, ``("main", "[0]")`` is the first item of a
    generator, etc. Exporters that build trees walk the prefix.
    """

    event_id: str
    """UUID — opaque, for dedup and idempotent partial flushes."""

    request_id: str
    """Per-``engine.start()`` call. Same as today's ``request_id``."""

    kind: EventKind

    op_name: Optional[str]
    """``None`` for synthetic events (group_start, group_end)."""

    ctx: Tuple[str, ...]
    """Scheduler context tuple. ``()`` for events not bound to a specific op."""

    timestamp: datetime
    """UTC. Use ``datetime.now(timezone.utc)``."""

    seq: int
    """Monotonic sequence number per emitter, for tiebreak when many events
    share a timestamp."""

    payload: Dict[str, Any] = field(default_factory=dict)
    """Kind-specific. See ``EventKind`` docstrings for the schema per kind."""

    def __lt__(self, other: "TraceEvent") -> bool:
        """Ordering by ``(timestamp, seq)`` so ``sorted()`` is stable."""
        if not isinstance(other, TraceEvent):
            return NotImplemented
        return (self.timestamp, self.seq) < (other.timestamp, other.seq)
