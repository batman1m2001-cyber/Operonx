"""The ClickHouse store — what the shared contract in ``test_run_store.py``
does not cover.

Offline (a fake client, always run):

* operonx imports, and the store constructs, without the driver and
  without a server;
* the row mapping: every column, the retention each origin gets, media
  refs in the rows and blobs in the media dir;
* the engine's path only enqueues: a hung or failing ClickHouse leaves a
  run's time alone, and costs drops, counted, past the bound;
* the schema is created once and versioned;
* ``trace_clickhouse:`` and ``run_store: {backend: clickhouse}`` resolve
  from ``resources.yaml``, ``${VAR}`` included.

Live (a throwaway server — see ``_clickhouse.py``; skipped without one):

* a real run reads back as the same tree the live trace builds;
* a ``Media`` WAV lands once in the media dir, typed and timed, whether a
  row saw it as ``Media`` or as the bytes the next op received;
* Langfuse and ClickHouse on one engine;
* TTL removes expired runs; ``prune_media`` removes orphaned blobs only;
* storing a run twice keeps one copy.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
import threading
import time
import wave
from pathlib import Path

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.media import Media
from operonx.core.registry import ResourceHub
from operonx.core.workflow_trace import OpExecution, UpstreamRef, WorkflowTrace
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs import RunFilter
from operonx.telemetry.runs.clickhouse import (
    FOREVER,
    NODE_COLUMNS,
    ROLLUP_COLUMNS,
    RUN_COLUMNS,
    ClickHouseRunStore,
    expires_at,
)
from tests.internal.telemetry._clickhouse import open_store

DAY = 86400.0

# -- a fake client -------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    """Records what the store sends. ``hang`` blocks every insert until
    set; ``fail`` makes every insert raise."""

    def __init__(self, hang: threading.Event = None, fail: bool = False):
        self.commands = []
        self.inserts = []
        self.hang = hang
        self.fail = fail
        self.versions = []

    def command(self, sql, parameters=None):
        self.commands.append(" ".join(sql.split()))

    def query(self, sql, parameters=None):
        if "max(version)" in sql:
            return _Result([[max(self.versions, default=0)]])
        return _Result([])

    def insert(self, table, rows, column_names=None, settings=None):
        if table.endswith("schema_version"):
            self.versions.extend(r[0] for r in rows)
            return
        if self.hang is not None:
            self.hang.wait()
        if self.fail:
            raise ConnectionError("clickhouse is down")
        self.inserts.append((table, list(column_names), rows, dict(settings or {})))

    def close(self):
        pass


def _store(tmp_path, client=None, **kw):
    kw.setdefault("flush_interval", 0.02)
    return ClickHouseRunStore(
        database="ox", media_dir=tmp_path / "media", client=client or FakeClient(), **kw
    )


def wav(seconds=1.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def _trace(trace_id="t-1", *, wall=1790467200.0, nodes=None, **meta):
    nodes = nodes or [
        OpExecution(
            op_id="g.speak#main",
            op_name="speak",
            op_full_name="g.speak",
            ctx=("main",),
            start_time=10.0,
            end_time=10.2,
            inputs={"text": "xin chào"},
            outputs={"audio": Media(wav(0.5), "audio/wav"), "n": 1},
            op_type="code",
        ),
        OpExecution(
            op_id="g.reply#main",
            op_name="reply",
            op_full_name="g.reply",
            ctx=("main",),
            start_time=10.2,
            end_time=10.5,
            inputs={"x": 1},
            outputs={"content": "hi", "cost_usd": 0.001, "usage": {"prompt_tokens": 3}},
            upstreams=[UpstreamRef("g.speak#main", "speak", "g.speak", "n", "x")],
            op_type="llm",
        ),
    ]
    return WorkflowTrace(
        trace_id=trace_id,
        workflow_name="g",
        started_at=10.0,
        ended_at=10.5,
        nodes=nodes,
        metadata=meta,
        wall_started_at=wall,
    )


# -- offline: imports and construction -------------------------------------------------


def test_the_store_constructs_and_consumes_without_the_driver(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "clickhouse_connect", None)  # import fails
    store = ClickHouseRunStore(host="127.0.0.1", port=1, media_dir=tmp_path, flush_interval=0.01)
    store.writer.retry_backoff = (0.001, 0.001)
    assert store.consume(_trace()) is True  # queued; nothing raised
    assert store.flush(timeout=5)
    assert store.writer.stats["dropped_failed"] == 1  # the ImportError was a failed write
    with pytest.raises(ImportError, match=r"operonx\[clickhouse\]"):
        store.count()
    store.writer.close(timeout=0.1)


def test_bad_names_and_ttl_are_refused(tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        ClickHouseRunStore(database="x; DROP", client=FakeClient(), media_dir=tmp_path)
    with pytest.raises(ValueError, match="ttl_days"):
        ClickHouseRunStore(ttl_days=-1, client=FakeClient(), media_dir=tmp_path)


# -- offline: rows -------------------------------------------------------------------------


def test_retention_per_origin_or_uniform():
    start = 1_000_000.0
    assert expires_at(start, "service", None) == int(start + 30 * DAY)
    assert expires_at(start, "playground", None) == int(start + 7 * DAY)
    assert expires_at(start, "job", None) == FOREVER  # jobs and evals are kept
    assert expires_at(start, "custom", None) == FOREVER  # an origin the policy omits is kept
    assert expires_at(start, "job", 3) == int(start + 3 * DAY)
    assert expires_at(start, "service", 0) == FOREVER
    assert expires_at(4.294e9, "service", 30) == FOREVER  # clamped to the DateTime range
    assert time.time() < expires_at(0.0, "service", 1)  # no start: counted from now


def test_build_maps_every_column_and_offloads_media(tmp_path):
    store = _store(tmp_path, ttl_days=None)
    t = _trace(origin="service", service="call", session_id="0912", tags=["a"])
    summary, run, nodes, rollups = store.build(t)
    row = dict(zip(RUN_COLUMNS, run))
    assert len(run) == len(RUN_COLUMNS)
    assert (row["trace_id"], row["origin"], row["name"], row["status"]) == (
        "t-1", "service", "call", "ok",
    )  # fmt: skip
    assert row["session_id"] == "0912" and row["cost_usd"] == pytest.approx(0.001)
    assert row["expires_at"] == int(1790467200.0 + 30 * DAY)
    assert json.loads(row["metadata"])["tags"] == ["a"]
    assert json.loads(row["meta"])["workflow_name"] == "g"
    assert row["version_dirty"] is None and row["llm_calls"] == 1 and row["tokens_in"] == 3

    assert [len(n) for n in nodes] == [len(NODE_COLUMNS)] * 2
    speak, reply = (dict(zip(NODE_COLUMNS, n)) for n in nodes)
    assert (speak["seq"], reply["seq"]) == (0, 1)
    assert speak["ctx"] == ["main"] and speak["run_started"] == 1790467200.0
    assert json.loads(speak["inputs"]) == {"text": "xin chào"}  # not \\u-escaped
    ref = json.loads(speak["outputs"])["audio"]
    assert ref["mime"] == "audio/wav" and ref["duration_s"] == pytest.approx(0.5, abs=1e-3)
    assert ref["store"] == "local" and store.media.exists(ref["$media"])
    assert json.loads(reply["upstreams"])[0]["from_op_id"] == "g.speak#main"

    by_op = {r[1]: dict(zip(ROLLUP_COLUMNS, r)) for r in rollups}
    assert by_op["reply"]["cost_usd"] == pytest.approx(0.001) and by_op["speak"]["count"] == 1
    assert by_op["reply"]["origin"] == "service" and by_op["reply"]["exact"] is True


def test_put_trace_inserts_nodes_then_rollups_then_runs_with_async_insert(tmp_path):
    client = FakeClient()
    store = _store(tmp_path, client=client)
    summary = store.put_trace(_trace())
    assert summary.trace_id == "t-1"
    assert [t for t, *_ in client.inserts] == ["ox.nodes", "ox.op_rollups", "ox.runs"]
    assert all(s == {"async_insert": 1, "wait_for_async_insert": 1} for *_, s in client.inserts)


def test_schema_is_created_once_and_versioned(tmp_path):
    client = FakeClient()
    store = _store(tmp_path, client=client)
    store.put_trace(_trace())
    store.put_trace(_trace("t-2"))
    creates = [c for c in client.commands if c.startswith("CREATE TABLE")]
    assert [c.split()[5] for c in creates] == [
        "ox.schema_version", "ox.runs", "ox.nodes", "ox.op_rollups",
    ]  # fmt: skip
    assert client.commands[0] == "CREATE DATABASE IF NOT EXISTS ox"
    assert client.versions == [1]
    # a second process finding version 1 creates nothing new
    again = _store(tmp_path, client=client)
    again.put_trace(_trace("t-3"))
    assert len([c for c in client.commands if "CREATE TABLE IF NOT EXISTS ox.runs" in c]) == 1


def test_batches_go_out_as_one_insert_per_table(tmp_path):
    client = FakeClient()
    store = _store(tmp_path, client=client, flush_interval=0.5)
    for i in range(5):
        store.consume(_trace(f"t-{i}"))
    assert store.flush(timeout=5)
    runs = [rows for t, _, rows, _ in client.inserts if t == "ox.runs"]
    assert sum(len(r) for r in runs) == 5 and len(runs) < 5


# -- offline: the engine's path only enqueues ------------------------------------------


@op
async def frames(n: int):
    for i in range(n):
        yield {"frame": i}


@op
def score(frame: int):
    return {"s": frame * 2}


@graph
def stream_flow(n):
    f = frames(n=n)
    s = score(frame=f["frame"])
    s["s"] >> PARENT["s"]
    START >> f >> s >> END


def _timed_run(engine, n, trace_id):
    async def go():
        t = time.perf_counter()
        handle = engine.start(inputs={"n": n}, trace_id=trace_id)
        await handle.collect()
        await handle._scheduler_task  # consumers have run
        return time.perf_counter() - t

    return asyncio.run(go())


def test_a_hung_clickhouse_does_not_slow_a_run_and_drops_past_the_bound(tmp_path):
    gate = threading.Event()
    store = _store(tmp_path, client=FakeClient(hang=gate), queue_size=3)
    engine = Operon(stream_flow, params={"n": None}, trace=[store])
    try:
        base = Operon(stream_flow, params={"n": None})
        _timed_run(base, 200, "warm")
        plain = min(_timed_run(base, 200, f"p{i}") for i in range(3))
        traced = [_timed_run(engine, 200, f"h{i}") for i in range(8)]
        assert max(traced) < plain * 3 + 0.2, (plain, traced)
        stats = store.writer.stats
        # a batch sits in the hung insert, three runs wait, the rest are dropped
        assert stats["submitted"] + stats["dropped_full"] == 8
        assert store.writer.queued == 3 and stats["dropped_full"] >= 1
    finally:
        gate.set()
        store.writer.close(timeout=1)


def test_a_failing_clickhouse_never_fails_a_run(tmp_path):
    store = _store(tmp_path, client=FakeClient(fail=True))
    store.writer.retry_backoff = (0.001, 0.001)
    engine = Operon(stream_flow, params={"n": None}, trace=[store])
    _timed_run(engine, 20, "f-1")
    assert store.flush(timeout=5)
    assert store.writer.stats["dropped_failed"] == 1
    store.writer.close(timeout=0.1)


def test_an_unreachable_server_costs_the_run_nothing(tmp_path):
    pytest.importorskip("clickhouse_connect")
    store = ClickHouseRunStore(
        host="127.0.0.1", port=1, media_dir=tmp_path, timeout=0.5, flush_interval=0.01
    )
    engine = Operon(stream_flow, params={"n": None}, trace=[store])
    t = time.perf_counter()
    _timed_run(engine, 50, "u-1")
    assert time.perf_counter() - t < 1.0
    store.writer.close(timeout=0.1)


# -- offline: configuration -------------------------------------------------------------------


def test_trace_clickhouse_and_run_store_resolve_from_yaml(tmp_path, monkeypatch):
    import operonx.telemetry  # noqa: F401 — registers the categories

    monkeypatch.setenv("OX_CH_PASSWORD", "s3cret")
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        textwrap.dedent(f"""
        trace_clickhouse:
          default: &clickhouse
            host: ch.internal
            port: 8443
            secure: true
            user: writer
            password: ${{OX_CH_PASSWORD}}
            database: traces
            ttl_days: 14
            media_dir: {tmp_path / "blobs"}
            media_threshold: 2048
            batch_size: 500
            flush_interval: 0.5
            queue_size: 50
        run_store:
          default:
            <<: *clickhouse
            backend: clickhouse
        """)
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))
    hub = ResourceHub.instance()
    for key in ("trace_clickhouse:default", "run_store:default"):
        s = hub.get(key)
        assert isinstance(s, ClickHouseRunStore), key
        assert (s.host, s.port, s.secure, s.user, s.password, s.database) == (
            "ch.internal", 8443, True, "writer", "s3cret", "traces",
        )  # fmt: skip
        assert s.ttl_days == 14 and s.media_threshold == 2048
        assert s.media.root == tmp_path / "blobs"
        assert (s.writer.batch_size, s.writer.flush_interval, s.writer.max_queue) == (500, 0.5, 50)
        assert s._ch is None  # nothing connected yet


# -- live --------------------------------------------------------------------------------------


@pytest.fixture
def live(request, tmp_path):
    return open_store(request, tmp_path)


def _rebuild(rec) -> WorkflowTrace:
    """A stored run back as a WorkflowTrace, to build its tree."""
    nodes = [
        OpExecution(
            op_id=r["op_id"],
            op_name=r["op_name"],
            op_full_name=r["op_full_name"],
            ctx=tuple(r["ctx"]),
            start_time=r["start_time"],
            end_time=r["end_time"],
            inputs=r["inputs"] or {},
            outputs=r["outputs"] or {},
            upstreams=[UpstreamRef(**u) for u in r["upstreams"]],
            status=r["status"],
            error=r["error"],
            op_type=r["op_type"],
            is_yield=r["is_yield"],
        )
        for r in rec.nodes
    ]
    return WorkflowTrace(
        trace_id=rec.summary.trace_id,
        workflow_name=rec.summary.workflow,
        started_at=rec.meta["started_at"],
        ended_at=rec.meta["ended_at"],
        nodes=nodes,
        metadata=rec.meta["metadata"],
        wall_started_at=rec.meta["wall_started_at"],
    )


@op
async def chunks(n: int):
    for i in range(n):
        await asyncio.sleep(0.001)
        yield {"chunk": f"c{i}"}


@op
async def tokens(chunk: str):
    for j in range(2):
        yield {"token": f"{chunk}.t{j}"}


@op
def ack(token: str):
    return {"done": token}


@graph
def nested(n):
    c = chunks(n=n)
    t = tokens(chunk=c["chunk"])
    a = ack(token=t["token"])
    a["done"] >> PARENT["done"]
    START >> c >> t >> a >> END


def test_a_run_reads_back_as_the_same_tree(live):
    from operonx.telemetry.consumers.langfuse import build_tree

    seen = []

    class Keep(Consumer):  # keeps the live trace to compare with
        def consume(self, trace):
            seen.append(trace)

    engine = Operon(nested, params={"n": None}, trace=[live, Keep()])
    _timed_run(engine, 3, "tree-1")
    rec = live.get_run("tree-1")
    assert rec is not None and len(rec.nodes) == len(seen[0].nodes) == 3 + 6 + 6

    def shape(tree):
        return {k: (n["parent"], n["name"], n["rule"]) for k, n in tree.items()}

    assert shape(build_tree(_rebuild(rec))) == shape(build_tree(seen[0]))
    assert [r["op_id"] for r in rec.nodes] == [n.op_id for n in seen[0].nodes]  # order kept


@op
def synth(text: str = ""):
    return {"audio": Media(wav(1.5), "audio/wav")}


@op
def listen(audio: bytes = b""):
    return {"heard": len(audio)}


@graph
def voice(text):
    s = synth(text=text)
    h = listen(audio=s["audio"])
    h["heard"] >> PARENT["heard"]
    START >> s >> h >> END


def test_media_audio_lands_once_typed_and_timed(live, tmp_path):
    engine = Operon(voice, params={"text": None}, trace=[live])

    async def go(tid):
        await engine.start(inputs={"text": "xin chào"}, trace_id=tid).collect()

    asyncio.run(go("v-1"))
    asyncio.run(go("v-2"))
    rec = live.get_run("v-1")
    by = {r["op_name"]: r for r in rec.nodes}
    produced = by["s"]["outputs"]["audio"]  # recorded as Media
    received = by["h"]["inputs"]["audio"]  # recorded as the bytes the op got
    for ref in (produced, received):
        assert ref["mime"] == "audio/wav" and ref["duration_s"] == pytest.approx(1.5, abs=1e-3)
        assert ref["sample_rate"] == 16000 and ref["channels"] == 1
    assert produced["$media"] == received["$media"]
    files = [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].suffix == ".wav"  # two runs, two refs each, one file
    with wave.open(str(files[0])) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(1.5)
    assert live.media.get(produced["$media"]) == wav(1.5)
    assert rec.media_root == str(tmp_path / "media")


def test_langfuse_and_clickhouse_on_one_engine(live):
    from operonx.telemetry.consumers.langfuse import LangfuseConsumer
    from tests.internal.telemetry.test_langfuse_consumer import FakeLangfuseClient

    client = FakeLangfuseClient()
    engine = Operon(
        nested, params={"n": None}, trace=[LangfuseConsumer(config={"client": client}), live]
    )
    _timed_run(engine, 2, "both-1")
    sent = [e for e in client.calls[0] if e["type"] != "trace-create"]
    rec = live.get_run("both-1")
    assert len(sent) == len(rec.nodes) == 2 + 4 + 4


def _aged(trace_id, days_ago, origin="service"):
    t = _trace(trace_id, wall=time.time() - days_ago * DAY, origin=origin, service="call")
    return t


def test_ttl_drops_expired_runs_from_every_table(request, tmp_path):
    store = open_store(request, tmp_path, ttl_days=None)  # per origin: service 30, job forever
    store.put_trace(_aged("old", 45))
    store.put_trace(_aged("new", 1))
    store.put_trace(_aged("kept", 400, origin="job"))
    for table in ("runs", "nodes", "op_rollups"):
        store._command(f"OPTIMIZE TABLE {store.database}.{table} FINAL")
    assert {s.trace_id for s in store.list_runs().items} == {"new", "kept"}
    n = store._query(f"SELECT count() FROM {store.database}.nodes WHERE trace_id = 'old'")
    r = store._query(f"SELECT count() FROM {store.database}.op_rollups WHERE trace_id = 'old'")
    assert n[0][0] == 0 and r[0][0] == 0


def test_prune_media_removes_only_orphans(live):
    live.put_trace(_trace("a"))
    other = _trace("b")
    other.nodes[0].outputs["audio"] = Media(wav(0.3), "audio/wav")
    live.put_trace(other)
    assert len(list(live.media.keys())) == 2
    assert live.prune_media(older_than_s=3600) == 0  # young blobs are left alone
    live.delete_runs(RunFilter(trace_ids=["b"]))
    assert live.prune_media(older_than_s=0) == 1
    (kept,) = [sha for sha, _ in live.media.keys()]
    rec = live.get_run("a")
    assert rec.nodes[0]["outputs"]["audio"]["$media"] == kept


def test_storing_a_run_twice_keeps_one_copy(live):
    live.put_trace(_trace("twice"))
    live.put_trace(_trace("twice"))
    assert live.count(RunFilter(trace_ids=["twice"])) == 1
    assert len(live.get_run("twice").nodes) == 2
    assert live.schema_version() == 1
