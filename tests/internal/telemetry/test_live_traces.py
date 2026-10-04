"""R2 live traces: a run is visible while it goes, and a killed run leaves
what it finished.

``Consumer.on_start`` and ``Consumer.on_execution`` are called as the run
starts and as each execution lands; the ClickHouse and SQL stores list the
run as ``running`` and append its executions, and the final write replaces
both. A process killed mid-run leaves the run listed as ``running`` with
the executions it completed.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core.workflow_trace import OpExecution, WorkflowTrace
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs import RunFilter
from tests.internal.telemetry._stores import PG_DSN, open_backend

LIVE_BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres", marks=pytest.mark.skipif(not PG_DSN, reason="set OPERONX_TEST_PG_DSN")
    ),
    "clickhouse",  # a throwaway server: see _clickhouse.py; skips when none answers
]

GATE: dict = {}


@op
async def first(x: int) -> dict:
    return {"y": x + 1}


@op
async def held(y: int) -> dict:
    await GATE["open"].wait()
    return {"z": y * 2}


@graph
def slow(x):
    f = first(x=x)
    h = held(y=f["y"])
    START >> f >> h >> END


class Recorder(Consumer):
    """A live consumer that keeps what it is handed, in order."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def on_start(self, trace):
        self.calls.append(("start", trace.trace_id, len(trace.nodes)))

    def on_execution(self, trace, execution):
        self.calls.append(("execution", execution.op_name, len(trace.nodes) - 1))

    def consume(self, trace):
        self.calls.append(("consume", trace.trace_id, len(trace.nodes)))


def test_consumer_hooks_default_to_nothing_and_mark_a_live_consumer():
    class EndOnly(Consumer):
        def consume(self, trace):
            return None

    assert EndOnly().live is False and Recorder().live is True
    trace = WorkflowTrace(trace_id="t", workflow_name="w", started_at=0.0, ended_at=0.0)
    node = OpExecution("w.a#main", "a", "w.a", ("main",), 0.0, 0.0, {}, {})
    assert EndOnly().on_start(trace) is None and EndOnly().on_execution(trace, node) is None


@pytest.mark.asyncio
async def test_live_consumer_is_called_as_the_run_goes():
    GATE["open"] = asyncio.Event()
    rec = Recorder()
    handle = Operon(slow, params={"x": None}, trace=rec).start({"x": 1}, trace_id="live-1")
    for _ in range(200):
        if any(c[0] == "execution" for c in rec.calls):
            break
        await asyncio.sleep(0.01)
    assert rec.calls == [("start", "live-1", 0), ("execution", "f", 0)], "seen before the run ends"
    GATE["open"].set()
    await handle.collect()
    assert rec.calls[-2:] == [("execution", "h", 1), ("consume", "live-1", 2)]


@pytest.mark.asyncio
async def test_a_failing_live_consumer_never_touches_the_run():
    class Broken(Consumer):
        def on_start(self, trace):
            raise RuntimeError("down")

        def on_execution(self, trace, execution):
            raise RuntimeError("down")

        def consume(self, trace):
            return None

    GATE["open"] = asyncio.Event()
    GATE["open"].set()
    out = await Operon(slow, params={"x": None}, trace=Broken()).run({"x": 1})
    assert out["z"] == 4 and "$errors" not in out


async def _poll(fn, timeout=10.0):
    """``fn()`` (a blocking store call) until it returns something truthy."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = await asyncio.to_thread(fn)
        if got:
            return got
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for the store")


@pytest.mark.parametrize("kind", LIVE_BACKENDS)
@pytest.mark.asyncio
async def test_trace_visible_while_running(kind, request, tmp_path):
    store = open_backend(kind, request, tmp_path)
    GATE["open"] = asyncio.Event()
    handle = Operon(slow, params={"x": None}, trace=store).start({"x": 1}, trace_id=f"vis-{kind}")

    def running():
        page = store.list_runs(RunFilter(status="running"))
        if not page.items:
            return None
        rec = store.get_run(f"vis-{kind}")
        return rec if rec is not None and rec.nodes else None

    rec = await _poll(running)
    assert rec.summary.status == "running"
    assert [n["op_name"] for n in rec.nodes] == ["f"]
    assert rec.nodes[0]["outputs"] == {"y": 2}
    GATE["open"].set()
    await handle.collect()

    def finished():
        rec = store.get_run(f"vis-{kind}")
        return rec if rec is not None and rec.summary.status == "ok" else None

    rec = await _poll(finished)
    assert [n["op_name"] for n in rec.nodes] == ["f", "h"]
    assert store.count(RunFilter(status="running")) == 0
    assert store.count() == 1


_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    sys.path.insert(0, {root!r})
    from operonx import END, START, Operon, graph, op
    from tests.internal.telemetry.test_live_traces import _store_from

    @op
    async def first(x: int) -> dict:
        return {{"y": x + 1}}

    @op
    async def forever(y: int) -> dict:
        print("READY", flush=True)
        await asyncio.sleep(600)
        return {{"z": y}}

    @graph
    def doomed(x):
        f = first(x=x)
        h = forever(y=f["y"])
        START >> f >> h >> END

    async def main():
        store = _store_from({spec!r})
        await Operon(doomed, params={{"x": None}}, trace=store).run({{"x": 1}}, trace_id="killed")

    asyncio.run(main())
    """
)


def _store_from(spec):
    """The store a child process writes to, from a small picklable spec."""
    kind = spec["kind"]
    if kind == "sqlite":
        from operonx.telemetry.runs.sqlite import SqliteRunStore

        return SqliteRunStore(path=spec["path"])
    from operonx.telemetry.runs.clickhouse import ClickHouseRunStore

    return ClickHouseRunStore(flush_interval=0.05, ttl_days=0, **spec["args"])


@pytest.mark.parametrize("kind", ["sqlite", "clickhouse"])
def test_killed_run_leaves_partial_trace(kind, request, tmp_path):
    store = open_backend(kind, request, tmp_path)
    if kind == "sqlite":
        spec = {"kind": "sqlite", "path": str(store.path)}
    else:
        args = {
            "host": store.host,
            "port": store.port,
            "user": store.user,
            "password": store.password,
            "database": store.database,
            "media_dir": str(tmp_path / "media"),
        }
        spec = {"kind": "clickhouse", "args": args}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    script = tmp_path / "child.py"
    script.write_text(_CHILD.format(root=root, spec=spec))
    proc = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "READY", proc.stderr.read()
        # the first op's record is written while the second one runs
        end = time.monotonic() + 15
        while time.monotonic() < end:
            rec = store.get_run("killed")
            if rec is not None and rec.nodes:
                break
            time.sleep(0.05)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == -signal.SIGKILL
    (summary,) = store.list_runs(RunFilter(trace_ids=["killed"])).items
    assert summary.status == "running", "nothing finished it: still listed as running"
    rec = store.get_run("killed")
    assert [n["op_name"] for n in rec.nodes] == ["f"]
    assert rec.nodes[0]["outputs"] == {"y": 2}


@pytest.mark.asyncio
async def test_live_can_be_turned_off(tmp_path):
    from operonx.telemetry.runs import open_run_store
    from operonx.telemetry.runs.sqlite import SqliteRunStore

    store = SqliteRunStore(path=tmp_path / "runs.sqlite", live=False)
    assert store.live is False and store.live_writer is None
    assert open_run_store({"backend": "sqlite", "path": str(tmp_path / "x.sqlite")}).live is True
    off = open_run_store({"backend": "sqlite", "path": str(tmp_path / "y.sqlite"), "live": False})
    assert off.live is False
    GATE["open"] = asyncio.Event()
    handle = Operon(slow, params={"x": None}, trace=store).start({"x": 1}, trace_id="off")
    await asyncio.sleep(0.4)
    assert store.list_runs().items == [], "written once the run ends, not before"
    GATE["open"].set()
    await handle.collect()
    assert store.get_run("off").summary.status == "ok"


@pytest.mark.parametrize("kind", LIVE_BACKENDS)
@pytest.mark.asyncio
async def test_a_running_run_counts_the_executions_landed_so_far(kind, request, tmp_path):
    store = open_backend(kind, request, tmp_path)
    GATE["open"] = asyncio.Event()
    handle = Operon(slow, params={"x": None}, trace=store).start({"x": 1}, trace_id=f"cnt-{kind}")

    def counted():
        page = store.list_runs(RunFilter(status="running"))
        return page.items if page.items and page.items[0].executions == 1 else None

    (summary,) = await _poll(counted)
    assert summary.trace_id == f"cnt-{kind}"
    GATE["open"].set()
    await handle.collect()
