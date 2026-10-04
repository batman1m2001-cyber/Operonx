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
from pathlib import Path
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
    "STATUS_RETRIED",
    # What every run in this process carries (the code's version, ...).
    "set_run_metadata",
    "run_metadata",
    "set_project_root",
    "project_root",
    "active_project",
    # Engine-internal ContextVars — not for author use.
    "_current_trace",
    "_current_op_ctx",
]


STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
#: An attempt that failed and was run again by the op's `retry=`. Not a
#: failure of the run: the attempt after it decides. Its `error` is kept.
STATUS_RETRIED = "retried"


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
        status:       One of `STATUS_OK` / `STATUS_ERROR` / `STATUS_CANCELLED`
                      / `STATUS_RETRIED` (an attempt `retry=` ran again; the
                      run's status ignores it).
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
    # The op's kind ("code", "llm", "graph", "branch", …) so a consumer
    # can type an observation (an LLM call is a generation) without
    # reaching back into the graph.
    op_type: str = ""
    # True for the record of ONE generator yield. A yield record carries
    # the ctx it dispatched, which makes it the container of everything
    # that ran for that item; a batch op in the same ctx is not.
    is_yield: bool = False
    # 1-based attempt of an op with `retry=`. Each failed attempt that was
    # retried has a record of its own, `op_id` suffixed `@<attempt>`; the
    # attempt that ends the op keeps the plain `op_id`.
    attempt: int = 1

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
        errors:        The run's ``"$errors"``: ``{op_full_name: {type,
                       message, count, first_ctx}}``, the same records
                       ``handle.errors`` returns (see
                       ``MemoryState.record_op_error``). It knows failures
                       no node shows: a subgraph whose child raised, a loop
                       stopped at its cap, a structured ``LLMOp`` step that
                       returned ``error``.
    """

    trace_id: str
    workflow_name: str
    started_at: float
    ended_at: float
    nodes: List[OpExecution] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Wall-clock anchor (`time.time()` taken with `started_at`). Every
    # perf timestamp in the run converts through it, so records keep
    # their cheap monotonic clock and a consumer still gets real dates.
    wall_started_at: float = 0.0
    errors: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000.0

    def tag(self, metadata: Dict[str, Any]) -> None:
        """Merge *metadata* onto the run before it ends, so every consumer
        sees it: ``tags`` extend the trace's list (no repeats), every other
        key is set. How a job names its runs (``job``, ``job_run``, …) and
        an eval its judges' (``role``, ``judged_trace``)."""
        extra = dict(metadata)
        tags = extra.pop("tags", None)
        self.metadata.update(extra)
        if tags:
            have = list(self.metadata.get("tags") or [])
            self.metadata["tags"] = have + [t for t in tags if t not in have]

    @property
    def status(self) -> str:
        """``"error"`` when anything in the run failed, else ``"ok"``.

        A failed node or an ``errors`` record: either alone is a failed
        run. The record is the one that catches a structured ``LLMOp``
        step whose node is ``ok`` but whose ``error`` output is set.
        """
        if self.errors or any(n.status == STATUS_ERROR for n in self.nodes):
            return "error"
        return "ok"

    @property
    def run_id(self) -> str:
        """The run's identity for external ids: consumers scope every
        observation id as ``f"{run_id}/{op_id}"`` so two runs of the same
        graph never share one (``op_id`` alone repeats every run)."""
        return self.trace_id

    def wall_of(self, perf: float) -> float:
        """Epoch seconds for a perf-counter timestamp taken in this run.
        Without an anchor (a trace built by hand) the value passes
        through unchanged."""
        if not self.wall_started_at:
            return perf
        return self.wall_started_at + (perf - self.started_at)

    # ── grep-style helpers (consumers use these all the time) ────────
    def by_op(self, op_name: str) -> List[OpExecution]:
        """All executions of a given op name (across ctxs)."""
        return [n for n in self.nodes if n.op_name == op_name]

    def roots(self) -> List[OpExecution]:
        """Executions with no upstream — graph entry points.

        An attempt that ``retry=`` ran again is not one: the attempt after
        it is. The same holds for :meth:`leaves`.
        """
        return [n for n in self.nodes if not n.upstreams and n.status != STATUS_RETRIED]

    def leaves(self) -> List[OpExecution]:
        """Executions no other node consumed — graph terminals.

        Computed by set-difference: any op_id that appears as
        `from_op_id` in some `UpstreamRef` is NOT a leaf.
        """
        producers = {u.from_op_id for n in self.nodes for u in n.upstreams}
        return [n for n in self.nodes if n.op_id not in producers and n.status != STATUS_RETRIED]


def all_edges(
    nodes: List[OpExecution],
) -> List[Tuple[str, str, str, str]]:
    """Flatten every `OpExecution.upstreams` into a single edge list.

    Returns `(from_op_id, from_key, to_op_id, to_key)` tuples. Proves
    that no separate `edges.jsonl` is needed — everything's inline on
    the nodes.
    """
    return [(u.from_op_id, u.from_key, n.op_id, u.to_key) for n in nodes for u in n.upstreams]


# ---------------------------------------------------------------------------
# Engine-internal ContextVars
# ---------------------------------------------------------------------------

# `Operon.start()` sets this at the top of the run task; `BaseOp.run()`
# reads it to append `OpExecution` records as ops complete. `None` means
# no tracing installed → recording is a cheap no-op guard.
_current_trace: ContextVar[Optional[WorkflowTrace]] = ContextVar(
    "operonx_workflow_trace",
    default=None,
)

# Per-op-invocation ctx — set by `BaseOp.run()` to the scheduler's
# `context_id` tuple (e.g. `("main",)`, `("main", "[0]")` for a streaming
# sub-context). Read via `format_ctx()` for display. Kept as a
# ContextVar so downstream tooling can inspect the caller's ctx if
# needed. Author code never touches this — the engine writes and clears.
_current_op_ctx: ContextVar[Optional[tuple]] = ContextVar(
    "operonx_op_ctx",
    default=None,
)


# ---------------------------------------------------------------------------
# Process-wide run defaults
# ---------------------------------------------------------------------------
#
# Some facts are true of every run a process makes: which commit of the
# code is running, which project it belongs to. `Application.bootstrap()`
# states them once here; `Operon.start()` merges the metadata into every
# trace it creates, so a consumer — local, Langfuse, a run store — sees
# the version without any author passing it along. The project root is
# where consumers resolve a relative directory (``.operonx/runs``).

_RUN_METADATA: Dict[str, Any] = {}
_PROJECT_ROOT: List[Any] = [None]


def set_run_metadata(**fields: Any) -> None:
    """Set metadata every later run in this process carries. A ``None``
    value removes the key."""
    for key, value in fields.items():
        if value is None:
            _RUN_METADATA.pop(key, None)
        else:
            _RUN_METADATA[key] = value


def run_metadata() -> Dict[str, Any]:
    """A copy of the process-wide run metadata."""
    return dict(_RUN_METADATA)


def set_project_root(path: Any) -> None:
    """The project this process runs — where relative consumer paths
    resolve. ``None`` forgets it."""
    _PROJECT_ROOT[0] = path


def project_root() -> Any:
    """The project root set by `set_project_root`, or ``None``."""
    return _PROJECT_ROOT[0]


#: The file that makes a directory a project (see ``operonx.app.manifest``).
PROJECT_FILE = "operonx.toml"


def active_project() -> Optional[Path]:
    """The project this process runs in, or ``None`` outside one.

    The root an ``Application`` set (:func:`set_project_root`); else the
    nearest directory at or above the working directory holding an
    ``operonx.toml`` — so a script run from anywhere inside a project
    belongs to it as a served run does. Looked up on each call: the working
    directory can change.
    """
    if _PROJECT_ROOT[0] is not None:
        return Path(_PROJECT_ROOT[0])
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / PROJECT_FILE).is_file():
            return candidate
    return None
