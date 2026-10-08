"""The loop a job runs: items → one run per item → results, with a record.

Everything a hand-written runner forgets is here once: a bound on how
many items are in flight, what a failure means for the rest of the run,
retries with a pause between them, resume by key, a line per item saying
what happened, and every result kept so the next step (``reduce``, or
the next run after ``--resume``) sees all of them.

An op that raises inside a run does not raise out of it — the engine
records the error on the trace and the run drains. So "did this item
fail" is read from the run's trace, not from an exception, and an item
whose run finished cleanly but produced nothing is ``empty``: recorded as
such, never mistaken for success.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, Optional

from operonx.app.serve.protocol import RunRequest
from operonx.app.serve.runner import RunTimeout, serve_session
from operonx.core.loggings import LOGGER
from operonx.core.workflow_trace import STATUS_ERROR, unhandled

from .items import iter_items
from .record import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    ITEM_TIMEOUT,
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    ItemResult,
    JobRun,
    RunRecord,
    done_keys,
    last_run,
    load_results,
)
from .session import JobSession

if TYPE_CHECKING:
    from .job import Job

__all__ = ["preflight_error", "run_job", "run_steps"]

#: The outcomes ``retry`` repeats and ``on_error="stop"`` stops on.
_RETRIABLE = (ITEM_FAILED, ITEM_TIMEOUT)


def _first_error(trace: Any, handle: Any = None) -> Optional[str]:
    """The first op that errored, as ``op: last line of its error``.

    The trace first, then the run's own record (``handle.errors``): a
    subgraph failing around its children leaves no errored trace node, and
    without the second look its item was recorded ``empty``.
    """
    errors = getattr(handle, "errors", None) or getattr(trace, "errors", None) or {}
    # a failure the graph handled (on_failure="error", an error edge) is in
    # the record and the trace, but it is not the item's failure
    handled = set(errors) - set(unhandled(errors))
    for node in getattr(trace, "nodes", None) or ():
        if node.status == STATUS_ERROR and node.op_full_name not in handled:
            return _as_item_error(node.op_name, node.error)
    for op_name, record in unhandled(errors).items():
        return _as_item_error(op_name.rsplit(".", 1)[-1], record["message"])
    return None


def _as_item_error(op_name: str, error: Optional[str]) -> str:
    text = (error or "").strip()
    last = text.splitlines()[-1] if text else "error"
    return f"{op_name}: {last}"


def _trace_metadata(job: "Job", run_id: str, key: Optional[str]) -> dict:
    """What a job's run carries on its trace: its origin (``job``, or
    ``eval``), ``job``, ``job_run`` and ``key`` — plus ``runbook`` and
    ``runbook_run`` (the stored names) when a job of ``steps`` started it —
    and the same pairs as tags. A job is never a span: the trace is the
    graph run's, exactly as when served, so this is the whole join between
    record and trace."""
    from ..origin import current_runbook, origin_metadata

    parent = current_runbook()
    return origin_metadata(
        getattr(job, "origin", "job"),
        job=job.name,
        job_run=run_id,
        key=key,
        runbook=parent[0] if parent else None,
        runbook_run=parent[1] if parent else None,
    )


def preflight_error(keys: Any, timeout: float = 2.0) -> Optional[str]:
    """Check the named resources answer before any item runs.

    Returns the reason as text, or ``None`` when all of them answered. A
    job whose endpoints are down should fail in two seconds, once — not
    item by item, each waiting out its own deadline.
    """
    if not keys:
        return None
    from operonx.core.registry import ResourceHub

    try:
        ResourceHub.instance().require_reachable(*keys, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return f"preflight: {type(exc).__name__}: {exc}"
    return None


async def _settle(handle: Any) -> None:
    """Wait for the run's teardown, not just its last frame, so the trace
    consumers are flushed before a job that is the whole process exits."""
    try:
        await handle.collect()
    except Exception:  # noqa: BLE001
        pass  # the record reads the trace, not this


def _plain(out: Any) -> Any:
    """A doorless run's outputs without the engine's ``$`` keys."""
    if isinstance(out, dict):
        return {k: v for k, v in out.items() if not str(k).startswith("$")}
    return out


async def _attempt(job: "Job", engine: Any, raw: Any, key: str, run_id: str) -> ItemResult:
    """One run for one item."""
    session = JobSession(key, meta={"job": job.name, "job_run": run_id})
    doorless = not job.has_doors()
    item = job.item_of(raw)
    try:
        if doorless:
            inputs = job.bind(item)
        else:
            inputs = dict(job.inputs)
            session.feed_nowait(item)
    except Exception as exc:  # noqa: BLE001 — the item does not fit the graph
        return ItemResult(key, ITEM_FAILED, error=f"input: {exc}")
    session.end_input()

    started = perf_counter()
    try:
        handle = await serve_session(
            engine,
            session,
            RunRequest(inputs=inputs),
            metadata=_trace_metadata(job, run_id, key),
            timeout=job.timeout,
        )
    except RunTimeout as exc:
        return ItemResult(
            key,
            ITEM_TIMEOUT,
            error=str(exc),
            trace_id=exc.trace_id,
            ms=(perf_counter() - started) * 1000,
            sent=session.sent,
        )
    except Exception as exc:  # noqa: BLE001
        return ItemResult(
            key,
            ITEM_FAILED,
            error=f"{type(exc).__name__}: {exc}",
            ms=(perf_counter() - started) * 1000,
        )
    ms = (perf_counter() - started) * 1000
    await _settle(handle)
    trace = getattr(handle, "trace", None)
    trace_id = getattr(trace, "trace_id", None)

    error = _first_error(trace, handle)
    if error:
        return ItemResult(
            key, ITEM_FAILED, error=error, trace_id=trace_id, ms=ms, sent=session.sent, trace=trace
        )

    if doorless:
        # No doors: the run's own outputs are the item's result. A run that
        # returns nothing is recorded `empty`, never mistaken for success.
        try:
            result = _plain(await handle.result())
        except Exception as exc:  # noqa: BLE001
            return ItemResult(
                key,
                ITEM_FAILED,
                error=f"{type(exc).__name__}: {exc}",
                trace_id=trace_id,
                ms=ms,
                trace=trace,
            )
        sent = 1 if result else 0
    else:
        sent = session.sent
        result = session.sent_items[0] if sent == 1 else (session.sent_items or None)

    status = ITEM_OK if sent else ITEM_EMPTY
    return ItemResult(
        key,
        status,
        trace_id=trace_id,
        ms=ms,
        sent=sent,
        trace=trace,
        result=result if sent else None,
    )


class _Output:
    """``Job(output=...)``: a ``.jsonl`` file (fresh per run, appended on
    ``--resume``) or a function ``(key, result)``."""

    def __init__(self, target: Any, resume: bool):
        self.fn = target if callable(target) else None
        self.fh = None
        if self.fn is None and target is not None:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = path.open("a" if resume else "w", encoding="utf-8")

    async def write(self, key: str, result: Any) -> None:
        if self.fn is not None:
            out = self.fn(key, result)
            if inspect.isawaitable(out):
                await out
        elif self.fh is not None:
            self.fh.write(
                json.dumps({"key": key, "result": result}, ensure_ascii=False, default=str)
            )
            self.fh.write("\n")
            self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()


async def _hook(job: "Job", result: ItemResult) -> None:
    """The run's ``on_item`` sees each outcome; it never breaks the run."""
    if job.on_item is None:
        return
    try:
        out = job.on_item(result)
        if inspect.isawaitable(out):
            await out
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"[job:{job.name}] on_item hook raised: {type(exc).__name__}: {exc}")


async def _reduce(job: "Job", run_id: str, results: Dict[str, Any]) -> Any:
    """Run the ``reduce`` graph once over every result, in key order."""
    engine = job.reducer()
    inputs = {k: v for k, v in job.inputs.items() if k in engine.graph.inputs}
    inputs["results"] = [results[k] for k in sorted(results)]
    session = JobSession("reduce", meta={"job": job.name, "job_run": run_id})
    session.end_input()
    handle = await serve_session(
        engine, session, RunRequest(inputs=inputs), metadata=_trace_metadata(job, run_id, "reduce")
    )
    out = await handle.result()
    await _settle(handle)
    error = _first_error(getattr(handle, "trace", None), handle)
    if error:
        raise RuntimeError(error)
    return _plain(out)


async def run_job(job: "Job", *, resume: bool = False) -> JobRun:
    """Run *job* once, one run per item (then ``reduce``), and return its record."""
    engine = job.engine()
    if job.reduce is not None:
        job.reducer()  # a reduce graph that cannot compile fails before any item
    root = job.records()

    unreachable = preflight_error(job.preflight)
    if unreachable:
        LOGGER.error(f"[job:{job.name}] {unreachable}")
        return RunRecord(root, job.name, meta=job.describe()).finish(RUN_FAILED, error=unreachable)

    previous = last_run(root, job.name) if resume else None
    if resume and previous is None:
        LOGGER.warning(
            f"[job:{job.name}] --resume: no earlier run under {root / job.name}; running everything"
        )
    done = done_keys(previous)
    if previous is not None and job.key is None:
        LOGGER.warning(
            f"[job:{job.name}] --resume without key=: every item has a new id, so nothing is skipped"
        )

    record = RunRecord(
        root,
        job.name,
        meta=job.describe(),
        resume_from=previous.run_id if previous else None,
        keep_results=job.keep_results,
    )
    # A resumed run starts with the results its predecessor kept for the keys
    # it skips, so this run's results.jsonl — and `reduce` — has all of them.
    if previous is not None and job.keep_results:
        for key, value in load_results(previous.path).items():
            if key in done:
                record.result(key, value)
    LOGGER.info(
        f"[job:{job.name}] {engine.name} (concurrency={job.concurrency}, on_error={job.on_error}"
        + (f", resume from {previous.run_id}" if previous else "")
        + ")"
    )

    begin = getattr(job, "begin", None)  # an Eval files its experiment here
    if begin is not None:
        begin(record.run_id, record.started)
    output = _Output(job.output, resume=previous is not None)
    attempts = job.retry.max_attempts if job.retry is not None else 1
    sem = asyncio.Semaphore(job.concurrency)
    stopped = asyncio.Event()
    tasks: set = set()
    source_error: Optional[str] = None

    async def one(raw: Any, key: str) -> None:
        try:
            result = None
            for attempt in range(1, attempts + 1):
                result = await _attempt(job, engine, raw, key, record.run_id)
                result.attempts = attempt
                if result.status not in _RETRIABLE or attempt == attempts:
                    break
                delay = job.retry.delay(attempt)
                LOGGER.warning(
                    f"[job:{job.name}] {key!r} {result.status} ({result.error}); "
                    f"retry {attempt}/{attempts - 1} in {delay:.2f}s"
                )
                await asyncio.sleep(delay)
            if result.status == ITEM_OK:
                try:
                    await output.write(key, result.result)
                except Exception as exc:  # noqa: BLE001 — the item failed to land
                    result.status = ITEM_FAILED
                    result.error = f"output: {type(exc).__name__}: {exc}"
            judge = getattr(job, "judge", None)  # an Eval judges the case here
            if judge is not None:
                try:
                    await judge(raw, result)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error(
                        f"[job:{job.name}] judging {key!r} failed: {type(exc).__name__}: {exc}"
                    )
            result.trace = None  # judged: a run holds no more traces than items in flight
            if result.status == ITEM_OK:
                record.result(key, result.result)
            record.item(result)
            result.result = None
            await _hook(job, result)
            if result.status in _RETRIABLE and job.on_error == "stop":
                stopped.set()
        finally:
            sem.release()

    try:
        async for raw in iter_items(job.items):
            if stopped.is_set():
                break
            try:
                key = job.key_of(raw)
            except Exception as exc:  # noqa: BLE001
                record.item(
                    ItemResult(
                        uuid.uuid4().hex[:12],
                        ITEM_FAILED,
                        error=f"key: {type(exc).__name__}: {exc}",
                    )
                )
                if job.on_error == "stop":
                    stopped.set()
                continue
            if key in done:
                skipped = ItemResult(key, ITEM_SKIPPED)
                record.item(skipped)
                await _hook(job, skipped)
                continue
            await sem.acquire()
            if stopped.is_set():
                sem.release()
                break
            task = asyncio.create_task(one(raw, key))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*list(tasks))
    except Exception as exc:  # noqa: BLE001
        # The items themselves broke — a bad line, a dead connection. What
        # was already in flight still finishes and is still recorded.
        source_error = f"items: {type(exc).__name__}: {exc}"
        LOGGER.error(f"[job:{job.name}] {source_error}")
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
    finally:
        output.close()

    failed = record.counts.get(ITEM_FAILED, 0) + record.counts.get(ITEM_TIMEOUT, 0)
    if stopped.is_set():
        status = RUN_STOPPED
    elif source_error or (failed and job.items_fail_run):
        status = RUN_FAILED
    else:
        status = RUN_OK

    extra: Dict[str, Any] = {}
    error = source_error
    if job.reduce is not None and status != RUN_STOPPED and not source_error:
        try:
            extra["reduced"] = await _reduce(job, record.run_id, load_results(record.path))
        except Exception as exc:  # noqa: BLE001
            error = f"reduce: {type(exc).__name__}: {exc}"
            LOGGER.error(f"[job:{job.name}] {error}")
            status = RUN_FAILED
    summarize = getattr(job, "summarize", None)  # an Eval's pass rate, and its gate
    if summarize is not None:
        more, status = summarize(status)
        extra.update(more or {})
    run = record.finish(status, error=error, extra=extra or None)
    LOGGER.info(f"[job:{job.name}] {run.summary()}  {run.path}")
    return run


async def run_steps(job: "Job", *, resume: bool = False, record_dir: Any = None) -> JobRun:
    """Run a job of ``steps``: each step in order; the first that is not
    ``ok`` stops the rest. Its record holds one line per step — status, the
    step's own run id — and each step keeps its own record as usual."""
    from ..origin import in_runbook

    record = RunRecord(job.records(), job.name, meta=job.describe())
    steps = []
    stopped = False
    with in_runbook(job.name, record.run_id):
        for step in job.steps:
            if stopped:
                record.item(ItemResult(step.name, ITEM_SKIPPED))
                steps.append({"name": step.name, "status": "skipped"})
                continue
            started = perf_counter()
            run = await step.run(resume=resume, record_dir=record_dir, on_item=None)
            ok = run.status == RUN_OK
            record.item(
                ItemResult(
                    step.name,
                    ITEM_OK if ok else ITEM_FAILED,
                    error=None if ok else (run.meta.get("error") or run.summary()),
                    ms=(perf_counter() - started) * 1000,
                )
            )
            steps.append({"name": step.name, "status": run.status, "run_id": run.run_id,
                          "path": str(run.path)})  # fmt: skip
            stopped = not ok
    run = record.finish(RUN_OK if not stopped else RUN_FAILED, extra={"steps": steps})
    LOGGER.info(f"[job:{job.name}] {run.summary()}  {run.path}")
    return run
