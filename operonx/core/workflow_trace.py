"""Workflow-first trace primitives — V3.

One `OpExecution` per op invocation carries everything a consumer needs:
op name, ctx (per-invocation discriminator for streaming / retries),
inputs, outputs, upstream data-flow edges, timings, and status.

A `WorkflowTrace` is a per-run container attached to
`ExecutionHandle.trace` — the engine appends to it automatically as ops
run; consumers read it after the run completes. See
`docs/TRACING_V3_DESIGN.md` for the whole design.

Nothing here does I/O or side effects — pure data + a couple of
grep-style helpers. Consumer subclasses (`LocalConsumer`,
`CallbotLocalConsumer`, `LangfuseConsumer`, …) live in
`operonx.telemetry.consumers` and consume `WorkflowTrace` objects.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "OpExecution",
    "UpstreamRef",
    "WorkflowTrace",
    "all_edges",
    "format_ctx",
    "make_op_id",
    # Status constants — narrow vocab so consumers can switch on them.
    "STATUS_OK",
    "STATUS_ERROR",
    "STATUS_CANCELLED",
    # Engine-internal ContextVars — not for author use.
    "_current_trace",
    "_current_op_ctx",
]


STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# ctx / op_id formatting — deterministic so upstream lookups always match.
# ---------------------------------------------------------------------------


def format_ctx(ctx: Tuple[str, ...]) -> str:
    """Serialize a runtime ctx tuple to a compact string.

    ``("main",)`` → ``"main"``; ``("main", "[3]")`` → ``"main.[3]"``.

    Kept as a helper so `make_op_id` and any consumer that wants to
    render ctx agree on format.
    """
    return ".".join(ctx) if ctx else ""


def make_op_id(op_full_name: str, ctx: Tuple[str, ...]) -> str:
    """Deterministic op_id — every (op_full_name, ctx) pair maps to
    a unique string. Producer `OpExecution.op_id` and downstream
    `UpstreamRef.from_op_id` are computed the same way → they match by
    construction, no lookup registry needed.
    """
    return f"{op_full_name}#{format_ctx(ctx)}"


@dataclass
class UpstreamRef:
    """One data-flow edge into an op invocation.

    Denormalised — carries producer op names alongside `from_op_id` so
    consumers rendering the graph don't need a second lookup pass just
    to display "which op fed this input".

    Naming mirrors `OpExecution`:

    * `from_op_name`      — producer's LOCAL name (display; e.g. `"classify"`).
    * `from_op_full_name` — producer's FULL path (uniqueness; e.g. `"engine.classify"`).
    * `from_op_id`        — derived from full_name + producer_ctx, matches
                            producer's `OpExecution.op_id` by construction.

    Attributes:
        from_op_id:        `OpExecution.op_id` of the producer invocation.
        from_op_name:      Producer's local name (display convenience).
        from_op_full_name: Producer's full name (matches `OpExecution.op_full_name`).
        from_key:          Producer's output key that supplied the value.
        to_key:            This op's input key that received the value.
    """

    from_op_id: str
    from_op_name: str
    from_op_full_name: str
    from_key: str
    to_key: str


@dataclass
class OpExecution:
    """One recording of a single op invocation.

    Streaming / async-generator ops produce ONE `OpExecution` per yield
    — the engine nests `ctx` (`("main", "[T]")` → `("main", "[T]", "[i]")`)
    so consumers can distinguish yields.

    Two names, deliberately:

    * `op_name`      — the LOCAL name (e.g. `"classify"`). Preferred for
                       display, grouping, and per-op formatter lookup.
    * `op_full_name` — the FULL graph-prefixed path (e.g. `"engine.classify"`).
                       Guaranteed unique across the DAG; the source of
                       truth for `op_id` derivation.

    Attributes:
        op_id:        Unique per invocation. Format:
                      `f"{op_full_name}#{format_ctx(ctx)}"` — deterministic,
                      matches `UpstreamRef.from_op_id` from downstream
                      consumers by construction.
        op_name:      Local name (display).
        op_full_name: Full graph-prefixed name (uniqueness).
        ctx:          Runtime ctx tuple — `("main",)` at root, deeper for
                      streaming sub-contexts.
        start_time:   `time.perf_counter()` at op entry (or per-yield
                      start for generators).
        end_time:     `time.perf_counter()` at op exit (or per-yield end).
        inputs:       `{arg_name → value}` snapshot at entry.
        outputs:      `{output_name → value}` snapshot at exit (or the
                      yielded value for one generator yield).
        upstreams:    Data-flow edges INTO this op — list not a single
                      parent, so multi-upstream aggregators are captured
                      losslessly.
        status:       One of `STATUS_OK` / `STATUS_ERROR` / `STATUS_CANCELLED`.
        error:        Formatted traceback string when `status ==
                      STATUS_ERROR`; `None` otherwise.
    """

    op_id: str
    op_name: str
    op_full_name: str
    ctx: Tuple[str, ...]
    start_time: float
    end_time: float
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    upstreams: List[UpstreamRef] = field(default_factory=list)
    status: str = STATUS_OK
    error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000.0


@dataclass
class WorkflowTrace:
    """One run's worth of `OpExecution` records + trace-level metadata.

    Live-appended by the engine as ops complete. Frozen once the run
    ends. Consumers read `nodes` (and optionally `metadata`) to produce
    their target-specific view.

    Attributes:
        trace_id:      Correlation key across a run (e.g. call_id).
        workflow_name: Name of the compiled graph — passed through as-is
                       so consumers can label the run.
        started_at:    `time.perf_counter()` at run start.
        ended_at:      `time.perf_counter()` at run end.
        nodes:         `OpExecution` records in append (start-time) order.
        metadata:      Free-form dict — `request_id`, `user_id`,
                       `session_id`, custom tags.
    """

    trace_id: str
    workflow_name: str
    started_at: float
    ended_at: float
    nodes: List[OpExecution] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000.0

    # ── grep-style helpers (consumers use these all the time) ────────
    def by_op(self, op_name: str) -> List[OpExecution]:
        """All executions of a given op name (across ctxs)."""
        return [n for n in self.nodes if n.op_name == op_name]

    def roots(self) -> List[OpExecution]:
        """Executions with no upstream — graph entry points."""
        return [n for n in self.nodes if not n.upstreams]

    def leaves(self) -> List[OpExecution]:
        """Executions no other node consumed — graph terminals.

        Computed by set-difference: any op_id that appears as
        `from_op_id` in some `UpstreamRef` is NOT a leaf.
        """
        producers = {u.from_op_id for n in self.nodes for u in n.upstreams}
        return [n for n in self.nodes if n.op_id not in producers]


def all_edges(
    nodes: List[OpExecution],
) -> List[Tuple[str, str, str, str]]:
    """Flatten every `OpExecution.upstreams` into a single edge list.

    Returns `(from_op_id, from_key, to_op_id, to_key)` tuples. Proves
    that no separate `edges.jsonl` is needed — everything's inline on
    the nodes.
    """
    return [
        (u.from_op_id, u.from_key, n.op_id, u.to_key)
        for n in nodes
        for u in n.upstreams
    ]


# ---------------------------------------------------------------------------
# Engine-internal ContextVars
# ---------------------------------------------------------------------------

# `Operon.start()` sets this at the top of the run task; `BaseOp.run()`
# reads it to append `OpExecution` records as ops complete. `None` means
# no tracing installed → recording is a cheap no-op guard.
_current_trace: ContextVar[Optional[WorkflowTrace]] = ContextVar(
    "operonx_workflow_trace", default=None,
)

# Per-op-invocation ctx — set by `BaseOp.run()` to the scheduler's
# `context_id` tuple (e.g. `("main",)`, `("main", "[0]")` for a streaming
# sub-context). Read via `format_ctx()` for display. Kept as a
# ContextVar so downstream tooling can inspect the caller's ctx if
# needed. Author code never touches this — the engine writes and clears.
_current_op_ctx: ContextVar[Optional[tuple]] = ContextVar(
    "operonx_op_ctx", default=None,
)
