"""What keeping trace media in ClickHouse costs, against a media directory.

A call-shaped graph: 40 turns, each synthesising a 1.5 s 16 kHz WAV clip
(48 KB) that the next op receives as bytes, so every clip is referenced
twice. Half the turns say one of 8 stock phrases (the same clip every
call), half say something new. Each run is written by two stores on one
ClickHouse: ``media="local"`` (blobs to a directory) and
``media="clickhouse"`` (blobs in the ``media`` table).

* The ``consume()`` call itself: what the run's own path pays.
* The writer thread per run: CPU (``thread_time``) and wall (CPU plus the
  inserts' round trips), measured around the writer's own sink.
* ``media.get``: one blob read back, as the studio does.

Needs a throwaway ClickHouse::

    docker run -d --rm --name ox-ch-test -p 127.0.0.1:18123:8123 \\
        -e CLICKHOUSE_USER=oxtest -e CLICKHOUSE_PASSWORD=oxtest-pw clickhouse/clickhouse-server
    OPERONX_TEST_CLICKHOUSE=http://oxtest:oxtest-pw@127.0.0.1:18123 \\
        uv run python scripts/bench_clickhouse_media.py
"""

import asyncio
import io
import os
import random
import statistics
import tempfile
import time
import uuid
import wave
from urllib.parse import urlparse

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.media import Media
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs.clickhouse import ClickHouseRunStore

TURNS, RUNS, STOCK = 40, 30, 8


def wav(seed: int, seconds: float = 1.5, rate: int = 16000) -> bytes:
    rnd = random.Random(seed)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(rnd.randbytes(int(seconds * rate) * 2))  # noise: incompressible
    return buf.getvalue()


STOCK_CLIPS = [wav(i) for i in range(STOCK)]
_fresh = iter([wav(10_000 + i) for i in range((RUNS + 2) * TURNS)])


@op
async def turns(n: int):
    for i in range(n):
        clip = random.choice(STOCK_CLIPS) if i % 2 else next(_fresh)
        yield {"audio": Media(clip, "audio/wav"), "text": f"turn {i}"}


@op
def play(audio: bytes = b"", text: str = ""):
    return {"played": len(audio)}


@graph
def call(n):
    t = turns(n=n)
    p = play(audio=t["audio"], text=t["text"])
    p["played"] >> PARENT["played"]
    START >> t >> p >> END


class Keep(Consumer):
    def __init__(self):
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


url = urlparse(os.environ.get("OPERONX_TEST_CLICKHOUSE", "http://default@127.0.0.1:8123"))
conn = dict(
    host=url.hostname,
    port=url.port or 8123,
    user=url.username or "default",
    password=url.password or "",
    ttl_days=0,
)
database = f"bench_{uuid.uuid4().hex[:8]}"
stores = {
    "local": ClickHouseRunStore(
        database=database, media="local", media_dir=tempfile.mkdtemp(), **conn
    ),
    "clickhouse": ClickHouseRunStore(database=database, media="clickhouse", **conn),
}

keep = Keep()
engine = Operon(call, params={"n": None}, trace=[keep])


async def one():
    h = engine.start(inputs={"n": TURNS}, trace_id=uuid.uuid4().hex)
    await h.collect()
    await h._scheduler_task


for _ in range(RUNS + 2):
    asyncio.run(one())
traces = keep.traces
blob_mb = (
    sum(len(n.outputs["audio"].data) for t in traces for n in t.nodes if n.op_name == "t") / 1e6
)
print(
    f"{len(traces)} runs, {len(traces[0].nodes)} executions and {TURNS} clips each, {blob_mb:.0f} MB of clips"
)

for s in stores.values():  # migrate, connect, warm
    s._write_batch(traces[:1])

print(
    f"\n  {'media':12s} {'consume() median us':>20s} {'p90 us':>8s} {'writer CPU ms/run':>18s} {'writer wall ms/run':>19s}"
)
for name, store in stores.items():
    calls, cpu, wall = [], [], []
    sink = store.writer.sink

    def timed_sink(batch, sink=sink):
        c0, w0 = time.thread_time(), time.perf_counter()
        sink(batch)
        cpu.append((time.thread_time() - c0, len(batch)))
        wall.append(time.perf_counter() - w0)

    store.writer.sink = timed_sink
    for t in traces[2:]:
        time.sleep(0.01)  # calls end apart, as on a call centre
        t0 = time.perf_counter()
        store.consume(t)
        calls.append(time.perf_counter() - t0)
    store.flush(120)
    n = sum(k for _, k in cpu)
    print(
        f"  {name:12s} {statistics.median(calls) * 1e6:20.1f} "
        f"{statistics.quantiles(calls, n=10)[-1] * 1e6:8.1f} "
        f"{sum(c for c, _ in cpu) / n * 1000:18.2f} {sum(wall) / n * 1000:19.2f}"
    )

ch = stores["clickhouse"]
rows = ch._query(f"SELECT count(), sum(size) FROM {database}.media")[0]
print(f"\nmedia table: {rows[0]} rows, {rows[1] / 1e6:.1f} MB raw; media stats {ch.media.stats}")
on_disk = ch._query(
    f"SELECT sum(data_compressed_bytes) FROM system.columns WHERE database = '{database}' AND table = 'media'"
)[0][0]
print(f"media table on disk: {on_disk / 1e6:.1f} MB compressed")
ping = []
for _ in range(50):
    t0 = time.perf_counter()
    ch._query("SELECT 1")
    ping.append(time.perf_counter() - t0)
print(f"SELECT 1 round trip: median {statistics.median(ping) * 1000:.2f} ms")
shas = [r[0] for r in ch._query(f"SELECT sha FROM {database}.media LIMIT 50")]
lat = []
for sha in shas:
    t0 = time.perf_counter()
    assert ch.media.get(sha)
    lat.append(time.perf_counter() - t0)
print(
    f"media.get: median {statistics.median(lat) * 1000:.2f} ms, max {max(lat) * 1000:.2f} ms over {len(lat)} blobs"
)
print("writer stats", {k: s.writer.stats for k, s in stores.items()})
ch._command(f"DROP DATABASE {database}")
for s in stores.values():
    s.close()
