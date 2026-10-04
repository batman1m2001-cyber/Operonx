"""`TraceView` — one run, as evaluators read it, live or stored.

An evaluator that asks for ``trace`` gets a :class:`TraceView`: every op
execution of the case's run, in start order, with its inputs, outputs,
status, timing and cost — and the helpers a trajectory check needs::

    def grounded(output, trace):
        ex = trace.last("extract_slots")
        return ex is not None and output["time"] == ex.outputs["time"]

The rows are the ones every run store keeps (``rows_of_trace``), so the
view of a live ``WorkflowTrace`` (an eval, just run) equals the view of
the same run read back from a store (a rescore, an online check):
:meth:`TraceView.from_trace` makes values JSON the way the files and
sqlite stores do. One difference is by design: a value over a store's
media threshold is a reference in the stored view and bytes in the live
one. The totals are :func:`~operonx.telemetry.runs.summarize` over the
same rows — a view's cost and tokens are the run store's.

Every execution is in :attr:`TraceView.rows`, a retried attempt included.
What a trajectory reads — :meth:`~TraceView.ops`, :meth:`~TraceView.path`,
:meth:`~TraceView.tool_calls`, :meth:`~TraceView.errors` — skips a retried
attempt and every step recorded under it: the attempt after it is the one
that counted. :meth:`~TraceView.llm_calls` and the totals keep them, since
those calls were made and paid for. A step an op records with ``child()``
(``OpRow.is_child``) is in :meth:`~TraceView.ops` by name, and in
:meth:`~TraceView.path` only with ``children=True``: the path is the
graph's ops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from operonx.core.workflow_trace import child_parent_id, superseded_ids
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs.model import RunSummary, rows_of_trace, summarize

__all__ = ["OpRow", "ToolCall", "TraceView", "run_cost"]

#: Executions that route or contain, not steps: left out of ``path()``
#: unless asked for by type.
STRUCTURAL = ("branch", "graph")


class _Plain(Consumer):
    """``Consumer.sanitize`` without a target: what ``rows_of_trace`` needs."""

    def consume(self, trace: Any) -> None:  # pragma: no cover — never consumes
        return None


@dataclass(frozen=True)
class OpRow:
    """One execution of one op. ``start`` is wall-clock epoch seconds;
    ``cost_usd`` is what the execution reported (``None``: it reported
    no price, or is not an LLM call — ``priced`` says which)."""

    op_id: str
    op_name: str
    op_full_name: str
    op_type: str
    ctx: Tuple[str, ...]
    inputs: Any
    outputs: Any
    status: str
    error: Optional[str]
    start: float
    duration_ms: float
    is_yield: bool = False
    #: The perf-counter start the run recorded; orders executions.
    start_time: float = 0.0
    #: The ``retry=`` attempt, 1-based.
    attempt: int = 1
    #: Semantic attributes a ``child()`` step set (``gen_ai.*``).
    attrs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_child(self) -> bool:
        """A step an op recorded itself with ``child()``, not an op."""
        return child_parent_id(self.op_full_name, self.ctx) is not None

    @property
    def is_llm_call(self) -> bool:
        """An execution that reports ``cost_usd`` — the rule the run store
        counts ``llm_calls`` by. A streaming LLM's token frames do not."""
        return isinstance(self.outputs, Mapping) and "cost_usd" in self.outputs

    @property
    def cost_usd(self) -> Optional[float]:
        cost = self.outputs.get("cost_usd") if isinstance(self.outputs, Mapping) else None
        return (
            float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        )

    @property
    def tokens_in(self) -> int:
        return _usage(self.outputs, "prompt_tokens")

    @property
    def tokens_out(self) -> int:
        return _usage(self.outputs, "completion_tokens")


@dataclass(frozen=True)
class ToolCall:
    """A tool call an LLM made: its name and arguments (a dict, or the raw
    text when it was not JSON), the call id, the LLM execution that made
    it, and — when an op returned the tool message for that id (the agents'
    dispatch does) — its ``result`` and ``status``."""

    name: str
    args: Any = field(default_factory=dict)
    id: Optional[str] = None
    op_id: Optional[str] = None
    result: Any = None
    status: Optional[str] = None


def _usage(outputs: Any, key: str) -> int:
    usage = outputs.get("usage") if isinstance(outputs, Mapping) else None
    if not isinstance(usage, Mapping):
        return 0
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _row(r: Mapping[str, Any]) -> OpRow:
    start_time = r.get("start_time")
    wall = r.get("wall_start")
    return OpRow(
        op_id=str(r.get("op_id") or ""),
        op_name=str(r.get("op_name") or ""),
        op_full_name=str(r.get("op_full_name") or ""),
        op_type=str(r.get("op_type") or ""),
        ctx=tuple(str(c) for c in (r.get("ctx") or ())),
        inputs=r.get("inputs"),
        outputs=r.get("outputs"),
        status=str(r.get("status") or "ok"),
        error=r.get("error"),
        start=float(wall if isinstance(wall, (int, float)) else (start_time or 0.0)),
        duration_ms=float(r.get("duration_ms") or 0.0),
        is_yield=bool(r.get("is_yield")),
        start_time=float(start_time or 0.0),
        attempt=int(r.get("attempt") or 1),
        attrs=dict(r.get("attrs") or {}),
    )


def _call_of(raw: Any, op_id: str) -> Optional[ToolCall]:
    """A tool call in either shape: flat (``name``, ``args``) or OpenAI's
    (``function.name``, ``function.arguments`` as JSON text)."""
    if not isinstance(raw, Mapping):
        return None
    fn = raw.get("function") if isinstance(raw.get("function"), Mapping) else {}
    name = raw.get("name") or fn.get("name")
    if not name:
        return None
    args = raw.get("args")
    if args is None:
        args = raw.get("input", fn.get("arguments"))
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            pass  # kept as the text the model wrote: a check can say so
    return ToolCall(
        name=str(name),
        args={} if args is None else args,
        id=raw.get("id") or raw.get("tool_call_id"),
        op_id=op_id,
    )


class TraceView:
    """See the module docstring. Build one with :meth:`from_trace`,
    :meth:`from_rows`, :meth:`from_record` or :meth:`from_store`."""

    def __init__(
        self,
        trace_id: str,
        *,
        rows: Optional[Sequence[Mapping[str, Any]]] = None,
        meta: Optional[Mapping[str, Any]] = None,
        trace: Any = None,
    ):
        self.trace_id = str(trace_id)
        self._raw = rows
        self._meta = dict(meta or {})
        self._trace = trace
        self._rows: Optional[List[OpRow]] = None
        self._counted: Optional[List[OpRow]] = None
        self._summary: Optional[RunSummary] = None

    # -- building ------------------------------------------------------------

    @classmethod
    def from_trace(cls, trace: Any) -> "TraceView":
        """A live ``WorkflowTrace``. Nothing is read until the view is."""
        return cls(str(trace.trace_id), trace=trace)

    @classmethod
    def from_rows(
        cls, rows: Sequence[Mapping[str, Any]], meta: Optional[Mapping[str, Any]] = None
    ) -> "TraceView":
        """A stored run: its rows (the shape ``nodes.jsonl`` holds) and its
        ``meta.json`` (``trace_id``, ``metadata``, ``duration_ms``, …)."""
        meta = dict(meta or {})
        return cls(str(meta.get("trace_id") or ""), rows=list(rows), meta=meta)

    @classmethod
    def from_record(cls, record: Any) -> "TraceView":
        """A :class:`~operonx.telemetry.runs.RunRecord` (``store.get_run``)."""
        meta = {"trace_id": record.summary.trace_id, **(record.meta or {})}
        return cls.from_rows(record.nodes, meta)

    @classmethod
    def from_store(cls, store: Any, trace_id: str) -> "TraceView":
        """The run *trace_id* from a run store."""
        record = store.get_run(trace_id)
        if record is None:
            raise LookupError(
                f"no run {trace_id!r} in {type(store).__name__}: was the eval traced into "
                "this store, and is the run still within its retention?"
            )
        return cls.from_record(record)

    def _load(self) -> List[OpRow]:
        if self._rows is None:
            if self._trace is not None:
                from operonx.telemetry.runs.model import meta_of_trace

                # the JSON a store writes, so live and stored values are equal
                raw = json.loads(json.dumps(rows_of_trace(self._trace, _Plain()), default=str))
                self._meta = json.loads(json.dumps(meta_of_trace(self._trace), default=str))
                self._trace = None
            else:
                raw = self._raw or []
            indexed = sorted(enumerate(raw), key=lambda p: (p[1].get("start_time") or 0.0, p[0]))
            self._raw = [r for _, r in indexed]
            self._rows = [_row(r) for r in self._raw]
        return self._rows

    # -- what it holds ---------------------------------------------------------

    @property
    def rows(self) -> List[OpRow]:
        """Every execution, in start order."""
        return self._load()

    @property
    def counted(self) -> List[OpRow]:
        """The executions that count, in start order: every row but a
        retried attempt and the steps recorded under one."""
        if self._counted is None:
            gone = superseded_ids(self.rows)
            self._counted = [r for r in self.rows if r.op_id not in gone]
        return self._counted

    @property
    def metadata(self) -> Dict[str, Any]:
        self._load()
        return dict(self._meta.get("metadata") or {})

    @property
    def workflow(self) -> str:
        self._load()
        return str(self._meta.get("workflow_name") or "")

    @property
    def summary(self) -> RunSummary:
        """The run store's summary of these rows (its totals, its counts)."""
        if self._summary is None:
            self._load()
            self._summary = summarize(self.trace_id, self._raw or [], self._meta)[0]
        return self._summary

    @property
    def duration_ms(self) -> float:
        return self.summary.duration_ms

    @property
    def cost_usd(self) -> Optional[float]:
        """Every priced call's cost; ``None`` when nothing was priced."""
        return self.summary.cost_usd

    @property
    def unpriced(self) -> int:
        """Calls that reported no price: a cost nobody measured."""
        return self.summary.unpriced

    @property
    def tokens_in(self) -> int:
        return self.summary.tokens_in

    @property
    def tokens_out(self) -> int:
        return self.summary.tokens_out

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    # -- reading it ------------------------------------------------------------

    @staticmethod
    def _named(row: OpRow, name: str) -> bool:
        return (
            row.op_name == name or row.op_full_name == name or row.op_full_name.endswith("." + name)
        )

    def ops(
        self,
        name: Optional[str] = None,
        *,
        type: Optional[str] = None,  # noqa: A002 — the row's field is op_type
        under: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[OpRow]:
        """Executions that count (:attr:`counted`) matching every filter
        given. *name* is an op's name or a dotted tail of its full name
        (``"handle.classify"``); *under* is an enclosing subgraph's name
        (the root's name — the variable that held the engine — never
        matches)."""
        out = []
        for r in self.counted:
            if name is not None and not self._named(r, name):
                continue
            if type is not None and r.op_type != type:
                continue
            if under is not None and under not in r.op_full_name.split(".")[1:-1]:
                continue
            if status is not None and r.status != status:
                continue
            out.append(r)
        return out

    def first(self, name: str) -> Optional[OpRow]:
        found = self.ops(name)
        return found[0] if found else None

    def last(self, name: str) -> Optional[OpRow]:
        found = self.ops(name)
        return found[-1] if found else None

    def path(
        self,
        *,
        types: Optional[Sequence[str]] = None,
        collapse: bool = False,
        children: bool = False,
    ) -> List[str]:
        """Op names in start order, of the executions that count. Without
        *types*, every execution but routing and containers (``branch``,
        ``graph``); with it, only those types. ``collapse`` merges
        consecutive repeats (a generator's yields). ``children`` adds the
        steps ops recorded with ``child()``, in their start order."""
        wanted = set(types) if types is not None else None
        names = [
            r.op_name
            for r in self.counted
            if (r.op_type in wanted if wanted is not None else r.op_type not in STRUCTURAL)
            and (children or not r.is_child)
        ]
        if collapse:
            names = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
        return names

    def llm_calls(self) -> List[OpRow]:
        """Executions that report ``cost_usd`` — LLM calls, as the run
        store counts them."""
        return [r for r in self.rows if r.is_llm_call]

    def tool_calls(self) -> List[ToolCall]:
        """Every tool call the counted LLM calls asked for, in order, with
        the tool message an op returned for it when one did."""
        answers: Dict[str, Mapping[str, Any]] = {}
        for r in self.counted:
            msg = r.outputs.get("tool_message") if isinstance(r.outputs, Mapping) else None
            if isinstance(msg, Mapping) and msg.get("tool_call_id"):
                answers[str(msg["tool_call_id"])] = msg
        out = []
        for r in self.counted:
            if not r.is_llm_call:
                continue
            for raw in r.outputs.get("tool_calls") or ():
                call = _call_of(raw, r.op_id)
                if call is None:
                    continue
                answer = answers.get(str(call.id)) if call.id else None
                if answer is not None:
                    call = ToolCall(
                        call.name,
                        call.args,
                        call.id,
                        call.op_id,
                        result=answer.get("content"),
                        status=answer.get("status"),
                    )
                out.append(call)
        return out

    def errors(self) -> List[OpRow]:
        """Executions that count and did not end ``ok``."""
        return [r for r in self.counted if r.status != "ok"]

    def as_text(self, limit: int = 4000) -> str:
        """The run as a model reads it (a judge's ``trace_summary``): one
        line per step of :meth:`path` — name, type, status, time, and its
        outputs clipped to one line — cut to *limit* characters."""
        lines = []
        for r in self.rows:
            if r.op_type in STRUCTURAL:
                continue
            out = json.dumps(r.outputs, ensure_ascii=False, default=str)
            out = out if len(out) <= 200 else out[:199] + "…"
            err = (
                f" error={r.error.strip().splitlines()[-1]}" if r.error and r.error.strip() else ""
            )
            lines.append(
                f"{r.op_name} ({r.op_type}) {r.status} {r.duration_ms:.0f} ms{err} → {out}"
            )
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    # -- identity --------------------------------------------------------------

    def _key(self) -> tuple:
        return (self.trace_id, self.workflow, self.metadata, self.duration_ms, self.rows)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TraceView):
            return NotImplemented
        return self._key() == other._key()

    __hash__ = None  # type: ignore[assignment] — mutable while lazy

    def __repr__(self) -> str:
        n = "?" if self._rows is None else len(self._rows)
        return f"TraceView({self.trace_id!r}, {n} executions)"


def run_cost(trace: Any) -> Dict[str, Any]:
    """A run's own LLM cost and tokens, read off a live ``WorkflowTrace``
    as the run store counts them (an execution reporting ``cost_usd`` is
    an LLM call; the cost is ``None`` when none was priced). Empty for a
    run with no LLM call. An eval reads it for each case's run and each
    judge's — two traces, so the system's cost never holds a judge's."""
    calls, cost, tokens_in, tokens_out = 0, None, 0, 0
    for node in getattr(trace, "nodes", None) or ():
        out = node.outputs
        if not isinstance(out, dict) or "cost_usd" not in out:
            continue
        calls += 1
        if isinstance(out["cost_usd"], (int, float)):
            cost = (cost or 0.0) + float(out["cost_usd"])
        usage = out.get("usage")
        if isinstance(usage, dict):
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
    if not calls:
        return {}
    return {"cost_usd": cost, "tokens_in": tokens_in, "tokens_out": tokens_out}
