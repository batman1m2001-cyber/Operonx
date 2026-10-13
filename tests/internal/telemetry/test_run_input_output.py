"""A run keeps what it was asked and what it answered.

The trace carries the run's inputs (the graph's parameters) and its output
(the root graph's declared cells when it ended); every run store keeps a
preview of each with the run's summary, so a list of runs can show them
(the studio's Runs screen, like Langfuse's trace list).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.workflow_trace import OpExecution, WorkflowTrace
from operonx.telemetry.runs.model import PREVIEW_LIMIT, meta_of_trace, run_preview, summarize
from tests.internal.telemetry._stores import BACKENDS, open_backend


@op
def greet(name: str, times: int = 1) -> dict:
    return {"text": f"hello {name}" * times}


@graph
def hello(name, times):
    PARENT.declare(reply=None)
    g = greet(name=name, times=times)
    g["text"] >> PARENT["reply"]
    START >> g >> END


def _run(engine, inputs, trace_id):
    async def go():
        handle = engine.start(inputs=inputs, trace_id=trace_id)
        await handle.collect()
        return handle.trace

    return asyncio.run(go())


def test_the_trace_carries_the_runs_inputs_and_output():
    trace = _run(Operon(hello(name=None, times=None)), {"name": "Minh", "times": 2}, "h-1")
    assert trace.inputs == {"name": "Minh", "times": 2}
    assert trace.output == {"reply": "hello Minhhello Minh"}


def test_meta_and_summary_hold_the_previews():
    trace = _run(Operon(hello(name=None, times=None)), {"name": "Minh", "times": 1}, "h-2")
    meta = meta_of_trace(trace)
    assert json.loads(meta["input"]) == {"name": "Minh", "times": 1}
    assert json.loads(meta["output"]) == {"reply": "hello Minh"}
    s, _ = summarize("h-2", [], meta)
    assert s.input == meta["input"] and s.output == meta["output"]


def test_a_preview_describes_media_and_stops_at_its_limit():
    audio = {"$media": "ab" * 32, "mime": "audio/wav", "size": 38992, "duration_s": 2.43425}
    assert run_preview({"audio": audio}) == '{"audio": "[audio/wav 2.4 s]"}'
    assert run_preview({"blob": b"\x00" * 9}) == '{"blob": "[9 bytes]"}'
    long = run_preview({"text": "x" * (PREVIEW_LIMIT * 2)})
    assert len(long) == PREVIEW_LIMIT and long.endswith("…")
    assert run_preview({}) is None and run_preview(None) is None


def test_a_preview_reads_a_state_object_as_its_fields():
    import dataclasses

    @dataclasses.dataclass
    class AgentState:
        current_state: str = "FINISH"
        should_hangup: bool = True

    assert json.loads(run_preview({"agent_state": AgentState()})) == {
        "agent_state": {"$type": "AgentState", "current_state": "FINISH", "should_hangup": True}}


def _trace(trace_id, inputs, output):
    node = OpExecution(
        op_id=f"g.step#main@{trace_id}", op_name="step", op_full_name="g.step", ctx=("main",),
        start_time=100.0, end_time=100.01, inputs={}, outputs={}, upstreams=[],
    )  # fmt: skip
    return WorkflowTrace(
        trace_id=trace_id, workflow_name="flow", started_at=100.0, ended_at=100.01,
        nodes=[node], metadata={"origin": "service", "service": "call"},
        wall_started_at=1790467200.0, inputs=inputs, output=output,
    )  # fmt: skip


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_store_lists_a_run_with_its_input_and_output(backend, request, tmp_path):
    store = open_backend(backend, request, tmp_path)
    store.consume(_trace("io-1", {"call_id": "c-1", "phone_number": "0912"}, {"result": "booked"}))
    store.consume(_trace("io-2", {}, {}))
    if hasattr(store, "writer"):
        store.writer.flush()
    rows = {s.trace_id: s for s in store.list_runs().items}
    assert json.loads(rows["io-1"].input) == {"call_id": "c-1", "phone_number": "0912"}
    assert json.loads(rows["io-1"].output) == {"result": "booked"}
    assert rows["io-2"].input is None and rows["io-2"].output is None
    assert json.loads(store.get_run("io-1").summary.output) == {"result": "booked"}


def test_a_sqlite_index_made_before_the_columns_gains_them(tmp_path):
    from operonx.telemetry.runs.sql import SUMMARY_COLUMNS
    from operonx.telemetry.runs.sqlite import SqliteRunStore

    path = tmp_path / "runs.sqlite"
    old = [c for c in SUMMARY_COLUMNS if c not in ("input", "output")]
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE runs ({', '.join(old)})")
    store = SqliteRunStore(path=path)
    store.consume(_trace("io-old", {"q": "hi"}, {"a": "there"}))
    (s,) = store.list_runs().items
    assert json.loads(s.input) == {"q": "hi"}


def test_the_langfuse_trace_gets_the_runs_input_and_output():
    from tests.internal.telemetry.test_langfuse_consumer import FakeLangfuseClient

    from operonx.telemetry.consumers.langfuse import LangfuseConsumer

    client = FakeLangfuseClient()
    trace = _run(Operon(hello(name=None, times=None)), {"name": "Minh", "times": 1}, "h-lf")
    LangfuseConsumer(config={"client": client}).consume(trace)
    body = client.calls[0][0]["body"]
    assert body["input"] == {"name": "Minh", "times": 1}
    assert body["output"] == {"reply": "hello Minh"}


def test_a_langfuse_listing_shows_the_traces_input_and_output():
    from operonx.telemetry.runs.langfuse import LangfuseRunStore

    store = LangfuseRunStore.__new__(LangfuseRunStore)
    s = store._summary_of_listing({
        "id": "lf-1", "name": "chat", "timestamp": "2026-10-11T00:00:00Z",
        "input": {"question": "hi"}, "output": "hello", "metadata": {},
    })  # fmt: skip
    assert json.loads(s.input) == {"question": "hi"} and json.loads(s.output) == "hello"


def test_live_a_v4_clickhouse_database_gains_the_columns(request, tmp_path):
    """A database a 1.21 writer made: `runs` without `input`/`output`, a run
    in it. The next writer migrates; the old run reads with none, the new
    one with both."""
    from operonx.telemetry.runs import clickhouse as chmod
    from tests.internal.telemetry._clickhouse import open_store

    store = open_store(request, tmp_path)
    real = chmod.MIGRATIONS
    chmod.MIGRATIONS = real[:4]
    try:
        store.put_trace(_trace("old", {}, {}))
        store.writer.flush()
        db = store.database
        store._command(f"ALTER TABLE {db}.runs DROP COLUMN input")
        store._command(f"ALTER TABLE {db}.runs DROP COLUMN output")
        assert store.schema_version() == 4
    finally:
        chmod.MIGRATIONS = real
    store._ready = False  # the next process to open it
    store.put_trace(_trace("new", {"q": "hi"}, {"a": "there"}))
    store.writer.flush()
    assert store.schema_version() == real[-1][0]
    rows = {s.trace_id: s for s in store.list_runs().items}
    assert rows["old"].input is None
    assert json.loads(rows["new"].input) == {"q": "hi"}


# ── a graph with doors: asked through its ingress, answering through its egress


from operonx.telemetry.consumer import Consumer  # noqa: E402


class _Keep(Consumer):
    """A trace consumer that keeps the traces it is given."""

    def __init__(self):
        super().__init__(config={})
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


def test_a_door_graphs_input_and_output_are_its_items():
    from operonx.app.serve import MemoryTransport, serve_session

    keep = _Keep()
    engine = Operon(_doors, trace=[keep])
    session = MemoryTransport().open(meta={})

    async def go():
        for word in ("a", "b"):
            await session.feed(word)
        session.end_input()
        await serve_session(engine, session)

    asyncio.run(go())
    (trace,) = keep.traces
    assert trace.inputs == {"item": ["a", "b"]}
    assert trace.output == {"item": ["A", "B"]}
    one = meta_of_trace(trace)
    assert json.loads(one["input"]) == {"item": ["a", "b"]}


@op
def _shout(word: str) -> dict:
    return {"reply": word.upper()}


from operonx.app.serve import egress, ingress  # noqa: E402


@graph
def _doors():
    src = ingress()
    loud = _shout(word=src["item"])
    out = egress(item=loud["reply"])
    START >> src >> loud >> out >> END
