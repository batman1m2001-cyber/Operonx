"""What an op body can know about its run, and the steps it records.

Both are information only — nothing here changes what the scheduler runs,
and control flow stays as visible nodes in the graph:

* :func:`run_context` returns the run and the invocation an op body is
  executing in, as a frozen :class:`RunContext`: the run's ids, the
  attempt, the deadline, the caller's ``context``, and an
  ``idempotency_key`` for external calls.
* :func:`child` records a step an op runs itself — a model call, a tool
  call, one turn of a loop — as an ``OpExecution`` under the op's own
  record, so a loop that lives inside one op is as visible in a trace as a
  graph of ops.

Plus the events ``engine.stream(mode="tasks")`` yields, one start and one
end per op invocation and per child execution: :class:`TaskStarted`,
:class:`TaskFinished`, :class:`TaskFailed`.

Engine-internal: ``_Frame`` (one per op invocation and per child execution;
the ``_current_frame`` ContextVar points at the innermost) and ``_RunInfo``
(the run-level facts ``Operon.start`` puts on the state).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Dict, Optional, Tuple

from operonx.core.workflow_trace import (
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_OK,
    OpExecution,
    make_op_id,
)

__all__ = [
    "ChildExecution",
    "RunContext",
    "TaskFailed",
    "TaskFinished",
    "TaskStarted",
    "child",
    "invocation_key",
    "run_context",
]


def invocation_key(run_id: Optional[str], op_full_name: str, ctx: Tuple[str, ...]) -> str:
    """The stable identity of one invocation: 32 hex characters.

    ``blake2b`` over ``[run_id, op_full_name, ctx]`` as JSON, so no two
    different triples join into the same text. The same op in the same ctx
    of the same run always gets the same key — on every retried attempt,
    and on a rerun of the run — and nothing else gets it. It is
    ``RunContext.idempotency_key`` and the id of an ``InterruptOp``'s event.
    """
    raw = json.dumps([run_id, op_full_name, list(ctx)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


@dataclass(frozen=True)
class RunContext:
    """The run and the invocation an op body is executing in.

    Attributes:
        run_id: The run's trace id (``start(trace_id=)``, else its
            ``request_id``): what every trace consumer files the run under.
            ``None`` for an op driven outside an engine run.
        thread_id: The ``session_id`` the caller passed to ``start()``, the
            conversation this run belongs to; ``None`` when none was passed.
        op_path: The execution's full name (``"graph.sub.op"``).
        ctx: The execution's ctx tuple (``("main", "[2]")``).
        attempt: The attempt running, 1-based (``@op(retry=)``).
        deadline: When the attempt's ``Timeout(run=)`` fires, on the
            ``time.monotonic()`` clock (the event loop's); ``None`` without
            one. :attr:`remaining` is the time left.
        context: The object passed as ``start(..., context=)`` (or
            ``run()``/``stream()``), as given; ``None`` when none was.
    """

    run_id: Optional[str]
    thread_id: Optional[str]
    op_path: str
    ctx: Tuple[str, ...]
    attempt: int = 1
    deadline: Optional[float] = None
    context: Any = None

    @property
    def idempotency_key(self) -> Optional[str]:
        """:func:`invocation_key` of this execution — the same on every
        attempt, different in every other run, op or ctx. Pass it to an
        external API that deduplicates (a payment, an email). ``None``
        without a ``run_id``."""
        if self.run_id is None:
            return None
        return invocation_key(self.run_id, self.op_path, self.ctx)

    @property
    def remaining(self) -> Optional[float]:
        """Seconds until :attr:`deadline` (negative once it passed), or ``None``."""
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()


def run_context() -> Optional[RunContext]:
    """The :class:`RunContext` of the op body calling it, or ``None``
    outside one (module level, a plain function call)."""
    frame = _current_frame.get()
    if frame is None:
        return None
    run = frame.run
    return RunContext(
        run_id=run.run_id if run is not None else None,
        thread_id=run.thread_id if run is not None else None,
        op_path=frame.full_name,
        ctx=frame.ctx,
        attempt=frame.attempt,
        deadline=frame.deadline,
        context=run.context if run is not None else None,
    )


# ---------------------------------------------------------------------------
# Task events — engine.stream(mode="tasks")
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskStarted:
    """An op invocation, or a child execution, began an attempt.

    ``op`` is its full name (a child's is its op's plus ``.<name>``),
    ``ctx`` its ctx tuple, ``attempt`` 1-based (``@op(retry=)``).
    """

    op: str
    ctx: Tuple[str, ...]
    attempt: int = 1


@dataclass(frozen=True)
class TaskFinished:
    """It ended without an error — a generator once, after its last item."""

    op: str
    ctx: Tuple[str, ...]
    attempt: int = 1
    duration_ms: float = 0.0


@dataclass(frozen=True)
class TaskFailed:
    """It ended with an error (``error`` is ``"TypeName: message"``), or was
    cancelled (``cancelled``, no ``error``). ``retrying`` means ``retry=``
    runs another attempt, which starts with its own :class:`TaskStarted`."""

    op: str
    ctx: Tuple[str, ...]
    attempt: int = 1
    duration_ms: float = 0.0
    error: str = ""
    cancelled: bool = False
    retrying: bool = False


# ---------------------------------------------------------------------------
# Child executions
# ---------------------------------------------------------------------------

#: What a child's name may be: it becomes a ctx segment ``name[n]`` and the
#: last part of an ``op_full_name``, so no ``.``, brackets or ``#``.
_CHILD_NAME = re.compile(r"^[^.\[\]#]+$")


class ChildExecution:
    """The handle :func:`child` yields. Set :attr:`outputs` (a dict; any
    other value is recorded as ``{"_": value}``) and :attr:`attrs`
    (semantic attributes such as ``gen_ai.operation.name``) before the
    block ends."""

    __slots__ = ("name", "outputs", "attrs")

    def __init__(self, name: str) -> None:
        self.name = name
        self.outputs: Any = {}
        self.attrs: Dict[str, Any] = {}


def child(
    name: str,
    inputs: Optional[Dict[str, Any]] = None,
    *,
    op_type: str = "",
    current: bool = True,
) -> "_ChildScope":
    """Record the block as a child execution of the op running it::

        @op
        async def agent(messages: list) -> dict:
            async with child("model", inputs={"messages": messages}, op_type="llm") as call:
                reply = await model(messages)
                call.outputs = reply
                call.attrs["gen_ai.operation.name"] = "chat"
            ...

    Args:
        name: What a trace viewer prints (``model``, ``lookup_order``). No
            ``.``, ``[``, ``]`` or ``#``: it becomes the ctx segment
            ``name[n]`` and the last part of the record's full name.
        inputs: What the step was given, recorded as its inputs.
        op_type: The step's kind (``llm``, ``tool``, ``turn``), so a
            consumer can type it (Langfuse makes ``llm`` a generation).
        current: Whether the code inside the block runs *as* the child:
            its own ``child()`` blocks nest under it and :func:`run_context`
            describes it. ``False`` records the step without that, for a
            block held open across an async generator's ``yield`` (a
            streamed model call): the consumer's code runs between the
            yields in the same context, and would otherwise run inside the
            step — and stay there if the generator is abandoned unclosed.

    The record's ctx is its parent's plus ``"<name>[<n>]"`` — the n-th
    child of that name under that parent — and its full name the parent's
    plus ``".<name>"``, so the parent is derived, never stored. The parent
    is the op's record; for a generator, the record of the yield being
    produced when the block opens; inside another child, that child. The
    op's ``@op(exclude=/include=)`` trace filter applies to the child's
    inputs and outputs.

    An exception inside the block is recorded as ``error`` and re-raised;
    a cancellation as ``cancelled``. Inside the block, :func:`run_context`
    describes the child, so each step has its own ``idempotency_key``.
    Outside a traced run it records nothing.

    Async only: a plain ``def`` op cannot use it, and a ``bound="cpu"``
    body runs in a thread, where records could interleave.
    """
    if not isinstance(name, str) or not _CHILD_NAME.match(name):
        raise ValueError(
            f"child name {name!r} is not usable: it becomes the ctx segment "
            f"'<name>[n]' and the last part of a full name, so it must be a non-empty "
            "string with no '.', '[', ']' or '#'. Use a plain name such as 'model'."
        )
    return _ChildScope(name, inputs, op_type, current)


class _ChildScope:
    """The async context manager :func:`child` returns."""

    __slots__ = ("_handle", "_inputs", "_op_type", "_current", "_parent", "_frame", "_start")

    def __init__(
        self, name: str, inputs: Optional[Dict[str, Any]], op_type: str, current: bool
    ) -> None:
        self._handle = ChildExecution(name)
        self._inputs = dict(inputs or {})
        self._op_type = op_type
        self._current = current
        self._parent: Optional[_Frame] = None
        self._frame: Optional[_Frame] = None
        self._start = 0.0

    async def __aenter__(self) -> ChildExecution:
        parent = _current_frame.get()
        if parent is None or parent.trace is None:
            return self._handle
        name = self._handle.name
        counts = parent.counts
        if counts is None:
            counts = parent.counts = {}
        index = counts.get(name, 0)
        counts[name] = index + 1
        frame = _Frame(
            f"{parent.full_name}.{name}",
            parent.child_base() + (f"{name}[{index}]",),
            parent.op,
            parent.trace,
            parent.run,
        )
        frame.attempt = parent.attempt
        frame.deadline = parent.deadline
        self._parent, self._frame = parent, frame
        self._start = perf_counter()
        if self._current:
            _current_frame.set(frame)
        if frame.trace._task_listeners:
            frame.trace.emit_task(TaskStarted(frame.full_name, frame.ctx, frame.attempt))
        return self._handle

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        frame = self._frame
        if frame is None:
            return False
        if self._current:
            # Restored by value, as BaseOp.run restores its own (a generator
            # closed by the loop's finalizer runs this in another context).
            _current_frame.set(self._parent)
        end = perf_counter()
        if exc is None:
            status, error = STATUS_OK, None
        elif isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
            status, error = STATUS_CANCELLED, None
        else:
            status = STATUS_ERROR
            error = "".join(traceback.format_exception(exc_type, exc, tb))
        trace = frame.trace
        handle = self._handle
        outputs = handle.outputs if isinstance(handle.outputs, dict) else {"_": handle.outputs}
        op = frame.op
        op_id = make_op_id(frame.full_name, frame.ctx)
        if frame.attempt > 1:
            # Numbering restarts per attempt, so the ctx (and the key) of a
            # retried step is stable; its record id must still be unique.
            op_id = f"{op_id}@{frame.attempt}"
        trace.record(
            OpExecution(
                op_id=op_id,
                op_name=handle.name,
                op_full_name=frame.full_name,
                ctx=frame.ctx,
                start_time=self._start,
                end_time=end,
                inputs=op._filter_for_trace(self._inputs),
                outputs=op._filter_for_trace(outputs),
                status=status,
                error=error,
                op_type=self._op_type,
                attempt=frame.attempt,
                attrs=dict(handle.attrs),
            )
        )
        if trace._task_listeners:
            ms = (end - self._start) * 1000.0
            if status == STATUS_OK:
                trace.emit_task(TaskFinished(frame.full_name, frame.ctx, frame.attempt, ms))
            else:
                trace.emit_task(
                    TaskFailed(
                        frame.full_name,
                        frame.ctx,
                        frame.attempt,
                        ms,
                        error=f"{type(exc).__name__}: {exc}" if status == STATUS_ERROR else "",
                        cancelled=status == STATUS_CANCELLED,
                    )
                )
        return False


# ---------------------------------------------------------------------------
# Engine-internal
# ---------------------------------------------------------------------------


class _RunInfo:
    """The run-level facts ``Operon.start`` puts on the state (``_run_info``)."""

    __slots__ = ("run_id", "thread_id", "context")

    def __init__(self, run_id: Optional[str], thread_id: Optional[str], context: Any) -> None:
        self.run_id = run_id
        self.thread_id = thread_id
        self.context = context


class _Frame:
    """One op invocation (or one child execution), as the code running
    inside it sees it.

    ``BaseOp.run`` sets one per invocation, after its inputs resolve, and
    restores the previous value when it ends. ``attempt`` and ``deadline``
    move with ``retry=``/``timeout=``. ``item`` is the yield a
    non-transient generator is producing (its children hang under that
    record); ``counts`` numbers the children per name under the current
    parent record, and restarts with each yield and each attempt.
    """

    __slots__ = (
        "full_name",
        "ctx",
        "op",
        "trace",
        "run",
        "attempt",
        "deadline",
        "item",
        "counts",
    )

    def __init__(
        self,
        full_name: str,
        ctx: Tuple[str, ...],
        op: Any,
        trace: Any,
        run: Optional[_RunInfo],
    ) -> None:
        self.full_name = full_name
        self.ctx = ctx
        self.op = op
        self.trace = trace
        self.run = run
        self.attempt = 1
        self.deadline: Optional[float] = None
        self.item: Optional[int] = None
        self.counts: Optional[Dict[str, int]] = None

    def child_base(self) -> Tuple[str, ...]:
        """The ctx of the record a child opened now hangs under."""
        if self.item is None:
            return self.ctx
        return self.ctx + (f"[{self.item}]",)


#: The innermost frame of the code running now; ``None`` outside an op.
_current_frame: ContextVar[Optional[_Frame]] = ContextVar("operonx_frame", default=None)


def _current_ctx() -> Optional[tuple]:
    """The ctx of the op invocation running now, or ``None``."""
    frame = _current_frame.get()
    return frame.ctx if frame is not None else None
