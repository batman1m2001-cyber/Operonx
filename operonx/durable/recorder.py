"""Recording a run into its journal, and replaying it on resume.

A :class:`RunRecorder` sits on a durable run's state (``state._durable``).
The scheduler drives every execution through :meth:`RunRecorder.wrap`
instead of ``op.run`` directly — one ``is None`` test when there is no
journal — and the state's write funnel reports each cell write to
:meth:`RunRecorder.on_write`, which files it under the execution making it
(a contextvar set around each step of that execution).

Names in the journal are relative to the root graph (``.a``, ``.sub.b``):
the root takes the name of the variable its engine is assigned to, which a
resuming process need not share.

An execution is ``(op, ctx, n, parent)``: the n-th start of the op at that
ctx inside its parent execution (``None`` at the root). A synthetic loop runs
every iteration at the same ctx, and each iteration is a new execution of the
loop op, so the ops inside it are told apart by their parent; n is the same
on resume because iterations are sequential.

Each yield and each end becomes a :class:`~.journal.Step` holding the writes
since the previous one and the event yielded, so the two are durable
together. On resume (:meth:`RunRecorder.restore`) every cell is set to its
last journalled value and every execution is looked up: one that ended
yields its journalled events again and is not run; one that yielded k times
and did not end runs again with its first k yields checked against the
journal (writes suppressed — the cells already hold them); any other runs.
See docs/RUNTIME_R3_PLAN.md §2.

A run also stops on purpose (D7, D8): an :class:`~operonx.InterruptOp` with
no answer parks — its question is journalled as a :data:`~.journal.PARKED`
step — and :meth:`RunRecorder.drain` asks for a stop. Either way the run is
*stopping*: an execution that would start now waits instead, the ones in
flight finish, and once every running execution is waiting (or only runs
ones that are) the engine stops the run, with no end recorded for any of
them. A resume runs them; ``answers`` give a parked interrupt its response.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
import time
import weakref
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Set, Tuple

from operonx.core.loggings import LOGGER
from operonx.core.ops._events import HandOff

from . import codec
from .journal import END, PARKED, Journal, JournalError, Step

__all__ = ["DURABILITY", "ON_RESUME", "NonDeterministicResume", "RunRecorder"]

#: When steps reach the journal.
DURABILITY = ("sync", "async", "exit")
#: What a resume does with an execution that yielded but did not end.
ON_RESUME = ("restart", "fail")

#: ``(op, ctx, n, parent)`` — one execution.
Key = Tuple[str, Tuple[str, ...], int, Any]

#: The execution whose writes are being made, in this task.
_EXEC: ContextVar[Optional["_Live"]] = ContextVar("operonx_durable_exec", default=None)
#: Whether this task is repeating a yield the journal holds (writes dropped).
_SUPPRESS: ContextVar[bool] = ContextVar("operonx_durable_suppress", default=False)


class NonDeterministicResume(RuntimeError):
    """A resumed execution did not repeat what the journal says it yielded."""


@dataclass
class _Live:
    """An execution running now: what it wrote and put on the stream since
    its last step, and the executions it runs inside (innermost first)."""

    key: Key
    within: Tuple[Key, ...]
    writes: List[Tuple[str, str, Tuple[str, ...], Any]] = field(default_factory=list)
    emits: List[Any] = field(default_factory=list)
    signals: Dict[Tuple[str, Tuple[str, ...]], Any] = field(default_factory=dict)

    def take(self) -> Tuple[List[Tuple[str, str, Tuple[str, ...], Any]], List[Any], Dict]:
        out = self.writes, self.emits, self.signals
        self.writes, self.emits, self.signals = [], [], {}
        return out


@dataclass
class _Recorded:
    """An execution as the journal has it."""

    events: List[Any] = field(default_factory=list)
    digests: List[str] = field(default_factory=list)
    signals: Dict[Tuple[str, Tuple[str, ...]], Any] = field(default_factory=dict)
    ended: bool = False
    #: What it wrote to its own outputs, and how it ended — for a replayed
    #: step's trace node.
    outputs: Dict[str, Any] = field(default_factory=dict)
    status: Optional[str] = None
    error: Any = None


def _digest(op: str, event: Any) -> str:
    """A yield's fingerprint, for checking it again on resume. Encoding it
    is also the first place a value that cannot be journalled shows: named
    here by op and var."""
    try:
        return codec.digest(event)
    except codec.CodecError as exc:
        outputs = event[1] if isinstance(event, tuple) and len(event) == 2 else None
        for var, value in outputs.items() if isinstance(outputs, dict) else ():
            try:
                codec.encode(value)
            except codec.CodecError:
                raise JournalError(
                    f"{op}.{var} wrote a {type(value).__name__}, which cannot be journalled "
                    f"({exc}). Return plain data, or keep the object in a resource and "
                    "pass its key"
                ) from exc
        raise JournalError(f"{op} yielded something that cannot be journalled ({exc})") from exc


class RunRecorder:
    """One durable run's recording (and, on resume, its replay)."""

    def __init__(
        self,
        journal: Journal,
        run_id: str,
        names: Dict[int, Tuple[str, str]],
        *,
        root: str = "",
        durability: str = "async",
        on_resume: str = "restart",
    ):
        if durability not in DURABILITY:
            raise ValueError(f"durability is one of {', '.join(DURABILITY)}, not {durability!r}")
        if on_resume not in ON_RESUME:
            raise ValueError(f"on_resume is one of {', '.join(ON_RESUME)}, not {on_resume!r}")
        self.journal = journal
        self.run_id = run_id
        self.durability = durability
        self.on_resume = on_resume
        self._root = root
        self._names = {idx: (self._rel(op), var) for idx, (op, var) in names.items()}
        self._started: Counter = Counter()  # (parent, op, ctx) -> executions started
        self._replay: Dict[Key, _Recorded] = {}
        # what each execution put on the run's stream queue itself — a
        # synthetic loop's per-iteration frames — with the executions it ran
        # inside, in journal order
        self._emitted: List[Tuple[Key, Tuple[Key, ...], List[Any]]] = []
        self._pending: List[Step] = []  # durability="exit"
        # the journal's one writer thread, shared by every run that records
        # into it: steps of many runs go in one transaction (`_SharedWriter`)
        self._writer: _SharedWriter = _writer_for(journal)
        # stopping on purpose: the status the run stops with, once asked
        self.stopping: Optional[str] = None
        #: Set by the engine when the run did stop so: ``"interrupted"`` or
        #: ``"drained"``. A run that ended before the stop came keeps None.
        self.stopped: Optional[str] = None
        self.parked: List[Dict[str, Any]] = []  # the questions it stopped on
        self.answers: Dict[str, Any] = {}  # a resume's, by interrupt id
        self.questions: Dict[Key, str] = {}  # journalled: execution -> interrupt id
        self._running: Dict[Key, Any] = {}  # execution -> the one it runs in
        self._children: Counter = Counter()
        self._waiting: Set[Key] = set()
        self._quiet = False
        #: Called once a stopping run has nothing left that is not waiting;
        #: None once the graph has returned.
        self.on_quiet: Optional[Callable[[], None]] = None
        #: Record each execution replayed from the journal on the run's
        #: trace: a resume with no trace of its own before (a fork, a run
        #: that crashed) still shows the whole run.
        self.trace_replays = False

    # -- the write funnel ----------------------------------------------------

    def suppressing(self) -> bool:
        return _SUPPRESS.get()

    def on_write(self, idx: int, ctx: Tuple[str, ...], value: Any) -> None:
        live = _EXEC.get()
        if live is not None:
            op, var = self._names[idx]
            live.writes.append((op, var, ctx, value))

    def _rel(self, name: str) -> str:
        """*name* relative to the root graph: ``.a`` for ``<root>.a``."""
        root = self._root
        if root and (name == root or name.startswith(root + ".")):
            return name[len(root) :]
        return name

    def _abs(self, name: str) -> str:
        return self._root + name if not name or name.startswith(".") else name

    def loop_signal(self, key: Tuple[str, Tuple[str, ...]], signal: Any) -> None:
        """How a synthetic loop's iteration ended (did a back-edge fire),
        filed under the iteration's execution."""
        live = _EXEC.get()
        if live is not None:
            live.signals[(self._rel(key[0]), key[1])] = signal

    def emitting(self, queue_: Any) -> "_Emitting":
        """*queue_* (the run's stream queue, handed to a synthetic loop),
        with every put filed under the execution making it."""
        return _Emitting(queue_)

    # -- driving an execution -------------------------------------------------

    async def wrap(self, op: Any, state: Any, ctx: Tuple[str, ...]) -> AsyncIterator[Any]:
        """``op.run(state, ctx)``, recorded — or replayed when it ended."""
        outer = _EXEC.get()
        parent = outer.key if outer is not None else None
        name = self._rel(op.full_name)
        n = self._started[(parent, name, ctx)]
        self._started[(parent, name, ctx)] = n + 1
        key: Key = (name, ctx, n, parent)
        done = self._replay.get(key)
        if done is not None and done.ended:
            if self.trace_replays:
                _trace_replay(op, ctx, done)
            self._re_emit(state, key)
            state._loop_signals.update(done.signals)
            for event in done.events:
                yield event
            return
        known = done.digests if done is not None else []
        if known and self.on_resume == "fail":
            raise NonDeterministicResume(
                f"{op.full_name} at {'/'.join(ctx)} had yielded {len(known)} time(s) and not "
                "ended when the run stopped; on_resume='fail' refuses to run it again"
            )
        live = _Live(key, (outer.key, *outer.within) if outer is not None else ())
        self._enter(key, parent)
        try:
            if self.stopping is not None:
                await self._wait(key)  # a stopping run starts nothing new
            run = self._live(op, state, ctx, live, known)
            try:
                async for event in run:
                    yield event
            finally:
                await run.aclose()  # here, in this task: see _live's own close
        finally:
            self._leave(key, parent)

    async def _live(
        self, op: Any, state: Any, ctx: Tuple[str, ...], live: _Live, known: List[str]
    ) -> AsyncIterator[Any]:
        """Run the execution, committing a step per yield and at its end."""
        source = op.run(state, ctx)
        # An op that is neither a generator nor a graph yields once: its event
        # is held until it has ended, so the event and the end are one step
        # and a crash between them cannot run it again.
        single = not getattr(op, "is_gen", True) and not hasattr(op, "_ops")
        try:
            held: List[Any] = []
            index = 0
            while True:
                repeating = index < len(known)
                exec_token = _EXEC.set(live)
                quiet_token = _SUPPRESS.set(repeating)
                try:
                    event = await source.__anext__()
                except StopAsyncIteration:
                    break
                finally:
                    _SUPPRESS.reset(quiet_token)
                    _EXEC.reset(exec_token)
                if event[1].__class__ is HandOff:
                    yield event  # not a step: the op moves to a task to retry
                    continue
                digest = _digest(op.full_name, event)
                if repeating:
                    if digest != known[index]:
                        raise NonDeterministicResume(
                            f"{op.full_name} at {'/'.join(ctx)}: yield {index} differs from the "
                            "journal's on resume — the op is not deterministic in what it yielded "
                            "before the run stopped. Make it so, or resume with on_resume='fail'"
                        )
                    live.take()  # already in the journal, and restored
                    yield event
                elif single:
                    held.append(event)
                else:
                    await self._commit(live, index, event=event, digest=digest)
                    yield event
                index += 1
            error_idx = state.schema.get_index(op.full_name, "error")
            error = state._cells[error_idx].contexts.get(ctx) if error_idx >= 0 else None
            await self._commit(
                live,
                END,
                event=tuple(held) if held else None,
                status="error" if error is not None else "ok",
                error=error,
                error_record=state._op_errors.get(op.full_name),
            )
        finally:
            # Closed here, in this task: a cancelled run's pump frame stays
            # alive in its traceback, and a generator it holds would never be
            # finalized — the op's `finally` would never run.
            await source.aclose()
        for event in held:
            yield event

    # -- stopping on purpose ------------------------------------------------------

    def answer(self, interrupt_id: str) -> Tuple[str, bool, Any]:
        """For the interrupt running now: the id its question has in the
        journal (*interrupt_id* when it was never asked), whether a resume
        answered it, and the answer."""
        live = _EXEC.get()
        if live is not None:
            interrupt_id = self.questions.get(live.key, interrupt_id)
        if interrupt_id in self.answers:
            return interrupt_id, True, self.answers[interrupt_id]
        return interrupt_id, False, None

    async def park(self, op: str, ctx: Tuple[str, ...], interrupt_id: str, payload: Any) -> None:
        """Journal the running interrupt's question and stop the run on it.
        Returns only by the run being stopped (cancelled)."""
        live = _EXEC.get()
        if live is None:
            raise RuntimeError(f"{op}: park() outside a durable execution")
        self.parked.append({"interrupt_id": interrupt_id, "op": op, "ctx": ctx, "payload": payload})
        self.questions[live.key] = interrupt_id
        await self._commit(live, PARKED, event=(interrupt_id, payload))
        self.stopping = "interrupted"
        await self._wait(live.key)

    def drain(self) -> None:
        """Stop the run: start nothing new, let what runs finish."""
        if self.stopping is None:
            self.stopping = "drained"
        self._check()

    def _enter(self, key: Key, parent: Any) -> None:
        self._running[key] = parent
        if parent is not None:
            self._children[parent] += 1

    def _leave(self, key: Key, parent: Any) -> None:
        self._running.pop(key, None)
        self._waiting.discard(key)
        if parent is not None:
            self._children[parent] -= 1
        self._check()

    async def _wait(self, key: Key) -> None:
        """Wait, as *key*, for the stopping run to be stopped."""
        self._waiting.add(key)
        self._check()
        await asyncio.get_running_loop().create_future()

    def _check(self) -> None:
        """Stop a stopping run once each running execution waits or runs
        only executions that do: nothing it holds can still move."""
        if self.stopping is None or self._quiet or self.on_quiet is None:
            return
        for key in self._running:
            if not self._children[key] and key not in self._waiting:
                return
        self._quiet = True
        asyncio.get_running_loop().call_soon(self._fire)

    def _fire(self) -> None:
        # read when it runs: the engine clears it once the graph returned,
        # and a stop must not land in the run's teardown
        if self.on_quiet is not None:
            self.on_quiet()

    async def _commit(self, live: _Live, index: int, **fields: Any) -> None:
        writes, emits, signals = live.take()
        op, ctx, occurrence, parent = live.key
        step = Step(
            op,
            ctx,
            index,
            writes,
            occurrence=occurrence,
            parent=parent,
            emits=emits,
            within=live.within if emits else (),
            signals=signals,
            **fields,
        )
        if self.durability == "sync":
            # committed before the scheduler sees the event; the commit is
            # shared with whatever other runs have waiting (group commit)
            await asyncio.wrap_future(self._writer.append(self.run_id, [step], wait=True))
        elif self.durability == "async":
            self._writer.append(self.run_id, [step])
        else:
            self._pending.append(step)

    def _end(self, status: str, trace: Optional[Dict[str, Any]]) -> "concurrent.futures.Future":
        """Hand the rest of the run to the writer: its steps (``"exit"``),
        the trace of a run that stopped (:mod:`.history`), then *status*.
        The future is done when all of it is written."""
        if self._pending:
            steps, self._pending = self._pending, []
            self._writer.append(self.run_id, steps)
        if trace is not None and hasattr(self.journal, "save_trace"):
            self._writer.keep_trace(self.run_id, trace)
        return self._writer.finish(self.run_id, status)

    def close(self, status: str, trace: Optional[Dict[str, Any]] = None) -> None:
        """Make every step durable, then record *status*. Blocking: the
        engine's cancel path calls it directly (`aclose` otherwise)."""
        self._end(status, trace).result()

    async def aclose(self, status: str, trace: Optional[Dict[str, Any]] = None) -> None:
        """``close``, awaited: no thread is held while the writer writes."""
        await asyncio.wrap_future(self._end(status, trace))

    # -- resume -----------------------------------------------------------------

    def restore(self, state: Any, steps: Sequence[Step], *, lenient: bool = False) -> None:
        """Put back what the journalled steps wrote, and index them for
        :meth:`wrap`. *lenient* (a resume told the graph changed) skips
        writes to cells the graph no longer has."""
        schema, cells = state.schema, state._cells
        for step in steps:
            for op, var, ctx, value in step.writes:
                idx = schema.get_index(self._abs(op), var)
                if idx < 0:
                    if lenient:
                        continue
                    raise JournalError(
                        f"the journal has a write to {self._abs(op)}.{var}, which this graph "
                        "does not have"
                    )
                cells[idx][ctx] = value
            key: Key = (step.op, step.ctx, step.occurrence, step.parent)
            if step.index == PARKED:
                self.questions[key] = step.event[0]
                continue
            if step.emits:
                self._emitted.append((key, tuple(step.within), list(step.emits)))
            rec = self._replay.setdefault(key, _Recorded())
            rec.signals.update({(self._abs(k[0]), k[1]): v for k, v in step.signals.items()})
            rec.outputs.update({var: value for op, var, _, value in step.writes if op == step.op})
            if step.index == END:
                rec.ended = True
                rec.status, rec.error = step.status, step.error
                rec.events.extend(step.event or ())  # a single-yield op's event
                idx = schema.get_index(self._abs(step.op), "error")
                if idx >= 0:
                    cells[idx][step.ctx] = step.error
                if step.error_record is not None:
                    state._op_errors[self._abs(step.op)] = step.error_record
            else:
                rec.events.append(step.event)
                rec.digests.append(step.digest)

    def _re_emit(self, state: Any, key: Key) -> None:
        """Put back on the stream queue what an ended execution — and every
        execution inside it, which replay does not visit — put there."""
        queue_ = getattr(state, "_stream_output_queue", None)
        if queue_ is None or not self._emitted:
            return
        for emitter, within, items in self._emitted:
            if emitter == key or key in within:
                for item in items:
                    queue_.put_nowait(item)


def _trace_replay(op: Any, ctx: Tuple[str, ...], done: _Recorded) -> None:
    """A trace node for an execution replayed from the journal: what it
    wrote, how it ended, no time of its own (``attrs["operonx.replayed"]``)."""
    from time import perf_counter

    from operonx.core.workflow_trace import (
        STATUS_ERROR,
        STATUS_OK,
        OpExecution,
        _current_trace,
        make_op_id,
    )

    trace = _current_trace.get()
    if trace is None:
        return
    now = perf_counter()
    failed = done.status == "error"
    trace.record(
        OpExecution(
            op_id=make_op_id(op.full_name, tuple(ctx)),
            op_name=op.name,
            op_full_name=op.full_name,
            ctx=tuple(ctx),
            start_time=now,
            end_time=now,
            inputs={},
            outputs=dict(done.outputs),
            status=STATUS_ERROR if failed else STATUS_OK,
            error=str(done.error) if failed and done.error is not None else None,
            op_type=getattr(op, "type", "") or "",
            attrs={"operonx.replayed": True},
        )
    )


class _Emitting:
    """A stream queue whose puts are recorded with the execution making them."""

    __slots__ = ("_queue",)

    def __init__(self, queue_: Any):
        self._queue = queue_

    def put_nowait(self, item: Any) -> None:
        live = _EXEC.get()
        if live is not None:
            live.emits.append(item)
        self._queue.put_nowait(item)


class _SharedWriter:
    """The writer of one journal, shared by every run recording into it.

    One thread takes every run's steps and ends, in the order they were
    given, and writes all it has waiting in one transaction where the
    journal can (``Journal.write``): a commit for many runs, where a thread
    and a commit per run serialized on SQLite's single writer. A run whose
    write fails fails alone: the batch is written op by op to find it, and
    that run's next step (or its end) raises the reason.
    """

    #: Seconds a writer with nothing to write waits before its thread ends;
    #: the next write starts another.
    IDLE = 5.0
    #: Seconds a batch nobody waits on (``"async"`` steps) waits for more
    #: before it is written: a run's steps, and its end, go in one commit
    #: instead of one each. A waited item (a ``"sync"`` step, an end) cuts
    #: it short.
    LINGER = 0.002

    def __init__(self, journal: Journal):
        # weakly: this writer is the journal's value in `_WRITERS`, and a
        # strong reference back would keep every journal (and a thread) alive
        self._journal = weakref.ref(journal)
        self.errors: Dict[str, BaseException] = {}
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def _put(self, item: tuple) -> None:
        with self._lock:
            self._q.put(item)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="journal-writer", daemon=True
                )
                self._thread.start()

    def append(
        self, run_id: str, steps: Sequence[Step], wait: bool = False
    ) -> Optional["concurrent.futures.Future"]:
        error = self.errors.get(run_id)
        if error is not None:
            raise JournalError(f"the journal stopped recording run {run_id}: {error}")
        done = concurrent.futures.Future() if wait else None
        self._put(("append", run_id, list(steps), done))
        return done

    def keep_trace(self, run_id: str, record: Dict[str, Any]) -> None:
        self._put(("trace", run_id, record, None))

    def finish(self, run_id: str, status: str) -> "concurrent.futures.Future":
        done: concurrent.futures.Future = concurrent.futures.Future()
        self._put(("status", run_id, status, done))
        return done

    def _loop(self) -> None:
        while True:
            try:
                first = self._q.get(timeout=self.IDLE)
            except queue.Empty:
                with self._lock:  # nothing came: end, unless a put just did
                    if self._q.empty():
                        self._thread = None
                        return
                continue
            batch = [first]
            while True:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if all(item[3] is None for item in batch):
                until = time.monotonic() + self.LINGER
                while (left := until - time.monotonic()) > 0:
                    try:
                        item = self._q.get(timeout=left)
                    except queue.Empty:
                        break
                    batch.append(item)
                    if item[3] is not None:
                        break
            try:
                self._write(batch)
            except BaseException as exc:  # noqa: BLE001 — the thread must outlive any batch
                LOGGER.exception("journal writer: a batch could not be written")
                for _op, _run, _arg, done in batch:
                    _settle(done, JournalError(f"the journal writer failed: {exc}"))

    def _write(self, batch: List[tuple]) -> None:
        """Write *batch*, then settle each of its futures — every one, so no
        run waits forever on a step the writer took."""
        journal = self._journal()
        if journal is None:  # its runs are gone with it
            for *_, done in batch:
                _settle(done, JournalError("the journal was closed"))
            return
        ops = [(op, run, arg) for op, run, arg, _ in batch if run not in self.errors]
        written = False
        write = getattr(journal, "write", None)
        if write is not None and ops:
            try:
                write(ops)
                written = True
            except BaseException:  # noqa: BLE001 — find the run at fault, below
                pass
        if not written:
            for op, run, arg in ops:
                if run in self.errors:
                    continue
                try:
                    if op == "append":
                        journal.append(run, arg)
                    elif op == "trace":
                        journal.save_trace(run, arg)
                    else:
                        journal.set_status(run, arg)
                except BaseException as exc:  # noqa: BLE001 — surfaced to the run
                    self.errors[run] = exc
                    LOGGER.error("journal for run %s stopped: %s", run, exc)
        del journal
        for op, run, _arg, done in batch:
            error = self.errors.pop(run, None) if op == "status" else self.errors.get(run)
            _settle(
                done,
                JournalError(f"the journal stopped recording run {run}: {error}")
                if error is not None
                else None,
            )


def _settle(done: Optional["concurrent.futures.Future"], error: Optional[BaseException]) -> None:
    """Resolve *done* — unless its waiter is gone: a cancelled run cancels
    the future it awaited (``asyncio.wrap_future``), and setting it then
    would raise in the writer's thread."""
    if done is None or done.cancelled():
        return
    try:
        if error is None:
            done.set_result(None)
        else:
            done.set_exception(error)
    except concurrent.futures.InvalidStateError:  # cancelled just now
        pass


#: The writer of each journal in this process, made on first use.
_WRITERS: "weakref.WeakKeyDictionary[Journal, _SharedWriter]" = weakref.WeakKeyDictionary()
_WRITERS_LOCK = threading.Lock()


def _writer_for(journal: Journal) -> _SharedWriter:
    with _WRITERS_LOCK:
        writer = _WRITERS.get(journal)
        if writer is None:
            writer = _WRITERS[journal] = _SharedWriter(journal)
        return writer
