"""What a ClickHouse trace consumer costs a run.

A streaming graph (2000 yields, a downstream op per yield: 4000 executions)
run with no consumer, a no-op consumer, and the ClickHouse consumer.

* A. spaced, interleaved: before every run the writer is drained and GC
  collected; a run pays only for what the consumer does on its own path.
  This is a call workload, where runs end seconds to minutes apart.
* B. back-to-back, per variant: runs follow each other with no gap, so the
  writer thread's work on run k (rows, blobs, insert) shares the GIL with
  run k+1. This is saturation, the worst case.
* The consume() call itself, and the writer thread's CPU per run.

Needs a throwaway ClickHouse::

    docker run -d --rm --name ox-ch-test -p 127.0.0.1:18123:8123 \\
        -e CLICKHOUSE_USER=oxtest -e CLICKHOUSE_PASSWORD=oxtest-pw clickhouse/clickhouse-server
    OPERONX_TEST_CLICKHOUSE=http://oxtest:oxtest-pw@127.0.0.1:18123 \\
        uv run python scripts/bench_clickhouse_consumer.py
"""

import asyncio
import gc
import os
import random
import statistics
import tempfile
import time
import uuid
from urllib.parse import urlparse

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs.clickhouse import ClickHouseRunStore

N, ROUNDS = 2000, 20


@op
async def frames(n: int):
    for i in range(n):
        yield {"frame": i, "text": f"chunk {i} xin chào"}


@op
def score(frame: int, text: str = ""):
    return {"s": frame * 2 + len(text)}


@graph
def flow(n):
    f = frames(n=n)
    s = score(frame=f["frame"], text=f["text"])
    s["s"] >> PARENT["s"]
    START >> f >> s >> END


class Noop(Consumer):
    def consume(self, trace):
        return None


def run(engine):
    async def go():
        t = time.perf_counter()
        h = engine.start(inputs={"n": N}, trace_id=uuid.uuid4().hex)
        await h.collect()
        await h._scheduler_task
        return time.perf_counter() - t

    return asyncio.run(go())


url = urlparse(os.environ.get("OPERONX_TEST_CLICKHOUSE", "http://default@127.0.0.1:8123"))
store = ClickHouseRunStore(
    host=url.hostname,
    port=url.port or 8123,
    user=url.username or "default",
    password=url.password or "",
    database=f"bench_{uuid.uuid4().hex[:8]}",
    ttl_days=0,
    media_dir=tempfile.mkdtemp(),
)
calls, cpu = [], []
_consume, _sink = store.consume, store._write_batch


def timed_consume(trace):
    t = time.perf_counter()
    try:
        return _consume(trace)
    finally:
        calls.append(time.perf_counter() - t)


def timed_sink(batch):
    t = time.thread_time()
    _sink(batch)
    cpu.append((time.thread_time() - t, len(batch)))


store.consume, store.writer.sink = timed_consume, timed_sink
engines = {
    "no consumer": Operon(flow, params={"n": None}),
    "no-op consumer": Operon(flow, params={"n": None}, trace=[Noop()]),
    "clickhouse consumer": Operon(flow, params={"n": None}, trace=[store]),
}
for e in engines.values():
    run(e)
store.flush(60)


def spaced():
    res = {k: [] for k in engines}
    for _ in range(ROUNDS):
        order = list(engines)
        random.shuffle(order)
        for k in order:
            store.flush(60)
            gc.collect()
            time.sleep(0.02)
            res[k].append(run(engines[k]) * 1000)
    return res


def back_to_back():
    res = {}
    for k, e in engines.items():
        store.flush(120)
        gc.collect()
        res[k] = [run(e) * 1000 for _ in range(ROUNDS)]
    store.flush(120)
    return res


def table(title, res):
    base = statistics.median(res["no consumer"])
    print(f"\n{title}: {N} yields = {2 * N} executions per run, {ROUNDS} runs each")
    print(f"  {'variant':22s} {'median ms':>10s} {'p25':>8s} {'p75':>8s} {'vs none':>9s}")
    for k, xs in res.items():
        q = statistics.quantiles(xs, n=4)
        med = statistics.median(xs)
        print(f"  {k:22s} {med:10.1f} {q[0]:8.1f} {q[2]:8.1f} {med - base:+9.1f}")


table("A. spaced, interleaved (writer idle at run start)", spaced())
table("B. back-to-back, per variant (writer busy with the previous run)", back_to_back())
print(
    f"\nconsume() call: median {statistics.median(calls) * 1e6:.0f} us, "
    f"max {max(calls) * 1e6:.0f} us over {len(calls)} runs"
)
per_run = sum(c for c, _ in cpu) / max(1, sum(n for _, n in cpu))
print(
    f"writer thread CPU: {per_run * 1000:.0f} ms per run, {per_run / (2 * N) * 1e6:.0f} us per execution"
)
print("writer stats", store.writer.stats)
store._command(f"DROP DATABASE {store.database}")
store.close()
