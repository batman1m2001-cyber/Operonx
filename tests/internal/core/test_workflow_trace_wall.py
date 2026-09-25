"""Wall clock and run identity on the workflow trace.

Records keep the perf counter; the trace carries one wall anchor and
converts on demand, so a consumer can write real dates without the
engine paying for a second clock read per op.
"""

import asyncio
import time

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.workflow_trace import WorkflowTrace

#: How wrong a `time.time()` bound is allowed to be — its own resolution.
#:
#: The trace anchors once with `time.time()` and converts from
#: `perf_counter`, and the two clocks are not equally precise: on Windows
#: `time.time()` ticks every 15.6ms while `perf_counter` resolves to 0.1us.
#: `before` is therefore quantised *down* to a tick boundary, so a converted
#: node time can sit legitimately outside `[before, after]` — and this flow
#: runs 3x5ms, about one tick end to end, so it straddles a boundary often.
#: Asserting exact containment made this test fail on identical code roughly
#: two runs in three.
_TICK = time.get_clock_info("time").resolution


@op
async def ticks(n: int):
    for i in range(n):
        await asyncio.sleep(0.005)
        yield {"i": i}


@op
def double(i: int):
    return {"d": i * 2}


@graph
def flow(n):
    t = ticks(n=n)
    d = double(i=t["i"])
    d["d"] >> PARENT["d"]
    START >> t >> d >> END


def test_trace_is_anchored_to_wall_time_and_records_convert():
    engine = Operon(flow, params={"n": 3})
    before = time.time()

    async def run():
        handle = engine.start(inputs={}, trace_id="wall-1")
        await handle.result()
        return handle.trace

    trace = asyncio.run(run())
    after = time.time()
    assert before - _TICK <= trace.wall_started_at <= after + _TICK
    assert trace.run_id == "wall-1"
    walls = [trace.wall_of(n.start_time) for n in trace.nodes]
    assert all(before - _TICK <= w <= after + _TICK for w in walls), (
        f"converted wall times {walls} outside [{before}, {after}] by more "
        f"than one {_TICK * 1000:.1f}ms clock tick"
    )
    # yields are ordered in wall time exactly as in perf time
    yields = [n for n in trace.nodes if n.op_name == "t"]
    assert len(yields) == 3
    assert [trace.wall_of(n.start_time) for n in yields] == sorted(
        trace.wall_of(n.start_time) for n in yields
    )
    assert trace.wall_of(yields[1].start_time) - trace.wall_of(yields[0].start_time) >= 0.004


def test_unanchored_trace_passes_perf_through():
    t = WorkflowTrace(trace_id="x", workflow_name="w", started_at=100.0, ended_at=101.0)
    assert t.wall_of(100.5) == 100.5
    assert t.run_id == "x"


def test_local_consumer_writes_wall_time(tmp_path):
    import json

    from operonx.telemetry.consumers.local import LocalConsumer

    engine = Operon(flow, params={"n": 2}, trace=LocalConsumer(config={"root": str(tmp_path)}))
    before = time.time()

    async def run():
        await engine.start(inputs={}, trace_id="wall-2").result()

    asyncio.run(run())
    rows = [
        json.loads(line) for line in (tmp_path / "wall-2" / "nodes.jsonl").read_text().splitlines()
    ]
    # Same clock-tick tolerance as above: these are `time.time()` bounds on
    # values converted from `perf_counter`.
    assert rows and all(before - _TICK <= r["wall_start"] <= time.time() + _TICK for r in rows)
    meta = json.loads((tmp_path / "wall-2" / "meta.json").read_text())
    assert before - _TICK <= meta["wall_started_at"] <= time.time() + _TICK
