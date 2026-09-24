"""The loop a job runs: source → one run per item → sink, with a record.

Everything a hand-written runner forgets is here once: a bound on how
many items are in flight, what a failure means for the rest of the run,
retries that are retries and not re-prompts, resume by key, and a line
per item saying what happened.

An op that raises inside a run does not raise out of it — the engine
records the error on the trace and the run drains. So "did this item
fail" is read from the run's trace, not from an exception, and an item
whose run finished cleanly but whose egress never fired is ``empty``:
recorded as such, never mistaken for success.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Optional

from operonx.core.loggings import LOGGER
from operonx.core.serve.protocol import RunRequest
from operonx.core.serve.runner import serve_session
from operonx.core.workflow_trace import STATUS_ERROR

from .record import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    ItemResult,
    JobRun,
    RunRecord,
    done_keys,
    last_run,
)
from .session import JobSession
from .sinks import as_sink
from .sources import as_source

if TYPE_CHECKING:
    from .job import Job

__all__ = ["ErrorPolicy", "parse_on_error", "run_per_item"]


@dataclass(frozen=True)
class ErrorPolicy:
    """``skip`` carries on, ``stop`` starts nothing new, ``retry:N`` tries an
    item N more times and then carries on."""

    mode: str
    retries: int = 0

    @property
    def attempts(self) -> int:
        return self.retries + 1


def parse_on_error(text: str) -> ErrorPolicy:
    text = (text or "").strip().lower()
    if text in ("skip", "stop"):
        return ErrorPolicy(text)
    if text.startswith("retry"):
        _, _, n = text.partition(":")
        try:
            retries = int(n) if n else 1
        except ValueError:
            raise ValueError(f"on_error {text!r}: retry wants a number, as in 'retry:3'") from None
        if retries < 1:
            raise ValueError(f"on_error {text!r}: retry wants at least 1")
        return ErrorPolicy("retry", retries)
    raise ValueError(f"on_error must be 'skip', 'stop' or 'retry:N', not {text!r}")


def _first_error(trace: Any) -> Optional[str]:
    """The first op that errored, as ``op: last line of its error``."""
    for node in getattr(trace, "nodes", None) or ():
        if node.status == STATUS_ERROR:
            text = (node.error or "").strip()
            last = text.splitlines()[-1] if text else "error"
            return f"{node.op_name}: {last}"
    return None


async def _attempt(job: "Job", engine: Any, sink: Any, raw: Any, key: str) -> ItemResult:
    """One run for one item."""
    session = JobSession(sink, key, meta={"job": job.name})
    inputs = dict(job.inputs)
    if job.item_input is None:
        session.feed_nowait(raw)
    else:
        inputs[job.item_input] = raw
    session.end_input()

    started = perf_counter()
    try:
        handle = await serve_session(engine, session, RunRequest(inputs=inputs))
    except Exception as exc:                              # noqa: BLE001
        return ItemResult(key, ITEM_FAILED, error=f"{type(exc).__name__}: {exc}",
                          ms=(perf_counter() - started) * 1000)
    ms = (perf_counter() - started) * 1000
    trace = getattr(handle, "trace", None)
    trace_id = getattr(trace, "trace_id", None)

    error = _first_error(trace)
    if error:
        return ItemResult(key, ITEM_FAILED, error=error, trace_id=trace_id, ms=ms, sent=session.sent)
    if session.sink_error:
        return ItemResult(key, ITEM_FAILED, error=f"sink: {session.sink_error}",
                          trace_id=trace_id, ms=ms, sent=session.sent)

    if job.item_input is not None:
        # No doors: the run's own result is the item's result.
        try:
            out = await handle.result()
            await sink.write(key, out)
        except Exception as exc:                          # noqa: BLE001
            return ItemResult(key, ITEM_FAILED, error=f"{type(exc).__name__}: {exc}",
                              trace_id=trace_id, ms=ms)
        session.sent += 1

    status = ITEM_OK if session.sent else ITEM_EMPTY
    return ItemResult(key, status, trace_id=trace_id, ms=ms, sent=session.sent)


async def run_per_item(job: "Job", *, resume: bool = False) -> JobRun:
    """Run *job* once, one run per item, and return its record."""
    engine = job.engine()
    source = as_source(job.source)
    sink = as_sink(job.sink)
    policy = parse_on_error(job.on_error)
    root = Path(job.record_dir)

    previous = last_run(root, job.name) if resume else None
    if resume and previous is None:
        LOGGER.warning(f"[job:{job.name}] --resume: no earlier run under {root / job.name}; "
                       "running everything")
    done = done_keys(previous)

    record = RunRecord(root, job.name, meta=job.describe(),
                       resume_from=previous.run_id if previous else None)
    LOGGER.info(f"[job:{job.name}] {source!r} -> {job.engine().name} -> {sink!r} "
                f"(per_item, concurrency={job.concurrency}, on_error={job.on_error}"
                + (f", resume from {previous.run_id}" if previous else "") + ")")

    sem = asyncio.Semaphore(job.concurrency)
    stopped = asyncio.Event()
    tasks: set = set()
    source_error: Optional[str] = None

    async def one(raw: Any, key: str) -> None:
        try:
            result = None
            for attempt in range(1, policy.attempts + 1):
                result = await _attempt(job, engine, sink, raw, key)
                result.attempts = attempt
                if result.status != ITEM_FAILED or attempt == policy.attempts:
                    break
                LOGGER.warning(f"[job:{job.name}] {key!r} failed ({result.error}); "
                               f"retry {attempt}/{policy.retries}")
            record.item(result)
            if result.status == ITEM_FAILED and policy.mode == "stop":
                stopped.set()
        finally:
            sem.release()

    try:
        async for raw in source.items():
            if stopped.is_set():
                break
            try:
                key = job.key_of(raw)
            except Exception as exc:                      # noqa: BLE001
                record.item(ItemResult(uuid.uuid4().hex[:12], ITEM_FAILED,
                                       error=f"key: {type(exc).__name__}: {exc}"))
                if policy.mode == "stop":
                    stopped.set()
                continue
            if key in done:
                record.item(ItemResult(key, ITEM_SKIPPED))
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
    except Exception as exc:                              # noqa: BLE001
        # The source itself broke — a bad line, a dead connection. What
        # was already in flight still finishes and is still recorded.
        source_error = f"source: {type(exc).__name__}: {exc}"
        LOGGER.error(f"[job:{job.name}] {source_error}")
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
    finally:
        try:
            await sink.close()
        except Exception as exc:                          # noqa: BLE001
            LOGGER.error(f"[job:{job.name}] sink close failed: {type(exc).__name__}: {exc}")

    if stopped.is_set():
        status = RUN_STOPPED
    elif source_error or record.counts.get(ITEM_FAILED, 0):
        status = RUN_FAILED
    else:
        status = RUN_OK
    run = record.finish(status, error=source_error)
    LOGGER.info(f"[job:{job.name}] {run.summary()}  {run.path}")
    return run
