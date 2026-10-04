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

    def __init__(
        self, hang: threading.Event = None, fail: bool = False, databases=(), grants_db=True
    ):
        self.databases = set(databases)
        self.grants_db = grants_db
        self.commands = []
        self.inserts = []
        self.hang = hang
        self.fail = fail
        self.versions = []

    def command(self, sql, parameters=None):
        sql = " ".join(sql.split())
        if sql.startswith("CREATE DATABASE"):
            if not self.grants_db:  # what ClickHouse answers: code 497
                raise PermissionError("Code: 497. Not enough privileges")
            self.databases.add(sql.split()[-1])
        self.commands.append(sql)

    def query(self, sql, parameters=None, column_formats=None):
        if sql.startswith("EXISTS DATABASE"):
            return _Result([[int(sql.split()[-1] in self.databases)]])
        if "max(version)" in sql:
            return _Result([[max(self.versions, default=0)]])
        if sql.startswith("SELECT data FROM") and ".media " in sql:
            assert column_formats == {"data": "bytes"}  # a String column read as bytes
            return _Result([[r[3]] for r in self.media_rows() if r[0] == parameters["s"]][:1])
        return _Result([])

    def media_rows(self):
        return [r for t, _, rows, _ in self.inserts if t.endswith(".media") for r in rows]

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
        "ox.schema_version", "ox.runs", "ox.nodes", "ox.op_rollups", "ox.media",
        "ox.experiments", "ox.experiment_items", "ox.scores", "ox.judge_cache",
    ]  # fmt: skip
    assert client.commands[0] == "CREATE DATABASE IF NOT EXISTS ox"
    assert client.versions == [1, 2, 3, 4]
    alters = [c for c in client.commands if c.startswith("ALTER TABLE")]
    assert [c.split()[8] for c in alters] == ["attempt", "attrs", "inputs_from"]
    # a second process finding version 1 creates nothing new
    again = _store(tmp_path, client=client)
    again.put_trace(_trace("t-3"))
    assert len([c for c in client.commands if "CREATE TABLE IF NOT EXISTS ox.runs" in c]) == 1


def test_a_user_granted_only_tables_writes_and_reads(tmp_path):
    # the database exists; the user may create tables in it, not databases
    client = FakeClient(databases={"ox"}, grants_db=False)
    store = _store(tmp_path, client=client)
    store.put_trace(_trace())
    assert not [c for c in client.commands if c.startswith("CREATE DATABASE")]
    assert [t for t, *_ in client.inserts] == ["ox.nodes", "ox.op_rollups", "ox.runs"]
    assert store.schema_version() == 4


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


# -- offline: media in ClickHouse ------------------------------------------------------------


def _ch(tmp_path, client=None, **kw):
    """A store keeping its blobs in ClickHouse; *tmp_path*/media must stay empty."""
    return _store(tmp_path, client=client, media="clickhouse", **kw)


def _with_audio(trace_id, *clips, wall=1790467200.0):
    """A run whose ops each output one clip."""
    nodes = [
        OpExecution(
            op_id=f"g.say#{i}",
            op_name="say",
            op_full_name="g.say",
            ctx=(str(i),),
            start_time=10.0 + i,
            end_time=10.5 + i,
            inputs={"i": i},
            outputs={"audio": Media(clip, "audio/wav")},
            op_type="code",
        )
        for i, clip in enumerate(clips)
    ]
    return _trace(trace_id, wall=wall, nodes=nodes, origin="service")


def _tables(client):
    return [t.split(".")[1] for t, *_ in client.inserts]


def test_a_v1_database_upgrades_by_creating_only_the_media_table_and_columns(tmp_path):
    client = FakeClient(databases={"ox"}, grants_db=False)
    client.versions = [1]  # runs, nodes and op_rollups already there
    store = _ch(tmp_path, client=client)
    store.put_trace(_trace())
    creates = [c.split()[5] for c in client.commands if c.startswith("CREATE TABLE")]
    assert creates[:2] == ["ox.schema_version", "ox.media"]  # then v3's tables
    assert client.versions == [1, 2, 3, 4] and store.schema_version() == 4
    ddl = next(c for c in client.commands if "ox.media" in c)
    assert "ReplacingMergeTree(expires_at)" in ddl and "ORDER BY sha" in ddl
    assert "TTL expires_at" in ddl and "PARTITION" not in ddl


def test_a_v2_database_upgrades_to_v3_by_creating_only_the_score_tables(tmp_path):
    from operonx.telemetry.scores.clickhouse import ClickHouseScoreStore

    client = FakeClient(databases={"ox"}, grants_db=False)  # a user granted only tables
    client.versions = [1, 2]  # a 1.14 database: runs, nodes, op_rollups, media
    store = ClickHouseScoreStore(database="ox", client=client)
    assert store.schema_version() == 4
    creates = [c.split()[5] for c in client.commands if c.startswith("CREATE TABLE")]
    assert creates == [
        "ox.schema_version", "ox.experiments", "ox.experiment_items", "ox.scores", "ox.judge_cache",
    ]  # fmt: skip
    assert not [c for c in client.commands if c.startswith("CREATE DATABASE")]
    assert client.versions == [1, 2, 3, 4]
    # the run store on the same database finds the newest and creates nothing
    runs = _ch(tmp_path, client=client)
    runs.put_trace(_trace())
    creates = [c.split()[5] for c in client.commands if c.startswith("CREATE TABLE")]
    assert creates[5:] == ["ox.schema_version"]  # only the version table's IF NOT EXISTS
    assert client.versions == [1, 2, 3, 4]


def _ddl_columns(client, table):
    ddl = next(c for c in client.commands if f"CREATE TABLE IF NOT EXISTS ox.{table} " in c)
    body = ddl.split("(", 1)[1]
    cols = []
    for part in body.split(", "):
        word = part.strip().split(" ", 1)[0]
        if word and word.isidentifier() and word not in ("INDEX", "CODEC", "DEFAULT"):
            cols.append(word)
    return cols


def test_the_score_store_inserts_the_columns_v3_creates(tmp_path):
    from operonx.telemetry.scores import Experiment, ExperimentItem, Score
    from operonx.telemetry.scores.clickhouse import ClickHouseScoreStore

    client = FakeClient()
    store = ClickHouseScoreStore(database="ox", client=client)
    store.put_experiment(Experiment("e1", "labels", started_at=1.0, metrics={"pass": {}}))
    store.put_items([ExperimentItem("e1", "a", output={"x": 1}, tags=["t"])])
    store.put_scores([Score("exact", experiment_id="e1", case_id="a", passed=True, created_at=1.0)])
    store.cache_put("k", {"passed": True})
    tables = {}
    for table, columns, rows, settings in client.inserts:
        tables[table.split(".")[1]] = columns
        assert len(rows[0]) == len(columns)
        assert settings == {"async_insert": 1, "wait_for_async_insert": 1}
    assert set(tables) == {"experiments", "experiment_items", "scores", "judge_cache"}
    for table, columns in tables.items():
        ddl = _ddl_columns(client, table)
        assert set(columns) == set(ddl) - {"written_at"}, table  # the server stamps written_at


def test_clickhouse_media_goes_in_the_batch_before_the_nodes(tmp_path):
    client = FakeClient()
    store = _ch(tmp_path, client=client, flush_interval=0.5)
    a, b = wav(0.2), wav(0.3)
    for i in range(4):
        store.consume(_with_audio(f"m-{i}", a, b if i % 2 else wav(0.1 + i)))
    assert store.flush(timeout=5)
    assert _tables(client) == ["media", "nodes", "op_rollups", "runs"]  # one batch, one each
    table, columns, rows, settings = client.inserts[0]
    assert columns == ["sha", "mime", "size", "data", "expires_at"]
    assert settings == {"async_insert": 1, "wait_for_async_insert": 1}
    assert sorted(len(r[3]) for r in rows) == sorted({len(a), len(b), len(wav(0.1)), len(wav(2.1))})
    assert {r[1] for r in rows} == {"audio/wav"}
    assert not (tmp_path / "media").exists()  # nothing on disk
    node = dict(zip(NODE_COLUMNS, client.inserts[1][2][0]))
    ref = json.loads(node["outputs"])["audio"]
    assert ref["store"] == "clickhouse" and ref["duration_s"] == pytest.approx(0.2, abs=1e-3)
    assert store.media.get(ref["$media"]) == a


def test_a_clip_this_process_wrote_is_not_written_again(tmp_path):
    client = FakeClient()
    store = _ch(tmp_path, client=client)
    clip = wav(0.4)
    store.put_trace(_with_audio("d-1", clip, clip))  # twice in one run: one row
    store.put_trace(_with_audio("d-2", clip))  # a later run: no row at all
    assert len(client.media_rows()) == 1
    assert _tables(client) == [
        "media",
        "nodes",
        "op_rollups",
        "runs",
        "nodes",
        "op_rollups",
        "runs",
    ]
    assert store.media.stats == {"written": 1, "bytes": len(clip), "skipped": 1}


def test_a_later_run_extends_a_shared_clips_expiry(tmp_path):
    client = FakeClient()
    store = _ch(tmp_path, client=client, ttl_days=30)
    clip, t0 = wav(0.4), 1790467200.0
    store.put_trace(_with_audio("e-1", clip, wall=t0))
    store.put_trace(_with_audio("e-2", clip, wall=t0 + 0.5 * DAY))  # within the slack: skipped
    store.put_trace(_with_audio("e-3", clip, wall=t0 + 3 * DAY))  # past it: written again
    expiries = [r[4] for r in client.media_rows()]
    assert expiries == [int(t0 + 31 * DAY), int(t0 + 34 * DAY)]
    # every run's blob outlives the run
    for tid, start in (("e-1", t0), ("e-2", t0 + 0.5 * DAY), ("e-3", t0 + 3 * DAY)):
        assert max(expiries) >= expires_at(start, "service", 30), tid


def test_the_seen_shas_are_bounded(tmp_path):
    client = FakeClient()
    store = _ch(tmp_path, client=client)
    store.media.seen_max = 2
    clips = [wav(0.1 * (i + 1)) for i in range(3)]
    for i, clip in enumerate(clips):
        store.put_trace(_with_audio(f"s-{i}", clip))
    store.put_trace(_with_audio("s-again", clips[0]))  # evicted: written again
    store.put_trace(_with_audio("s-kept", clips[2]))  # still seen: skipped
    assert len(store.media._seen) == 2 and len(client.media_rows()) == 4


def test_a_failed_media_insert_is_retried_and_its_blobs_not_marked_written(tmp_path):
    class FlakyClient(FakeClient):
        failures = 1

        def insert(self, table, rows, column_names=None, settings=None):
            if table.endswith(".media") and self.failures:
                self.failures -= 1
                raise ConnectionError("clickhouse blinked")
            super().insert(table, rows, column_names, settings)

    client = FlakyClient()
    store = _ch(tmp_path, client=client)
    store.writer.retry_backoff = (0.001, 0.001)
    clip = wav(0.3)
    store.consume(_with_audio("r-1", clip))
    assert store.flush(timeout=5)
    assert store.writer.stats["failed_batches"] == 0 and store.writer.stats["written"] == 1
    assert len(client.media_rows()) == 1 and _tables(client)[0] == "media"
    assert store.media.get(client.media_rows()[0][0]) == clip


def test_blob_bytes_held_by_a_batch_are_bounded(tmp_path):
    client = FakeClient()
    clips = [wav(0.5 + i / 100) for i in range(6)]  # one distinct clip per run
    bound = 2 * len(clips[0])
    store = _ch(tmp_path, client=client, media_batch_bytes=bound)
    store._write_batch([_with_audio(f"b-{i}", clip) for i, clip in enumerate(clips)])  # one batch
    media = [rows for t, _, rows, _ in client.inserts if t.endswith(".media")]
    assert sum(len(r) for r in media) == 6 and len(media) == 3  # out every second run
    # never more than the bound plus the one run that crossed it
    assert all(sum(len(x[3]) for x in rows) < bound + len(clips[-1]) for rows in media)
    assert _tables(client).index("nodes") > max(
        i for i, t in enumerate(_tables(client)) if t == "media"
    )  # still all before the nodes


def test_clickhouse_media_get_exists_and_refuses_bad_shas(tmp_path):
    client = FakeClient()
    store = _ch(tmp_path, client=client)
    sha = store.media.put(b"\x89PNG\r\n\x1a\n" + b"0" * 2000)  # outside a batch: written now
    assert client.media_rows()[0][:3] == [sha, "image/png", 2008]
    assert store.media.get(sha).startswith(b"\x89PNG")
    assert store.media.get("0" * 64) is None
    assert store.media.get("../etc/passwd") is None and store.media.exists("nope") is False
    assert store.get_run("t-1") is None


def test_media_is_local_by_default_and_checked(tmp_path):
    from operonx.telemetry.media import LocalMediaStore

    assert isinstance(_store(tmp_path).media, LocalMediaStore)
    with pytest.raises(ValueError, match="media"):
        _store(tmp_path, media="s3")


def test_media_clickhouse_resolves_from_yaml(tmp_path):
    import operonx.telemetry  # noqa: F401 — registers the categories
    from operonx.telemetry.runs.clickhouse import ClickHouseMediaStore

    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        "trace_clickhouse:\n  default:\n    host: ch.internal\n    media: clickhouse\n"
        "run_store:\n  default:\n    backend: clickhouse\n    host: ch.internal\n"
        "    media: clickhouse\n"
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))
    for key in ("trace_clickhouse:default", "run_store:default"):
        s = ResourceHub.instance().get(key)
        assert isinstance(s.media, ClickHouseMediaStore), key
        assert s.media.name == "clickhouse" and s._ch is None


def test_project_stores_reads_clickhouse_media_from_clickhouse(tmp_path):
    from operonx.telemetry.runs import project_stores
    from operonx.telemetry.runs.clickhouse import ClickHouseMediaStore

    writer_client = FakeClient()
    writer = _ch(tmp_path, client=writer_client)
    clip = wav(0.7)
    writer.put_trace(_with_audio("p-1", clip))

    root = tmp_path / "proj"
    root.mkdir()
    (root / "operonx.toml").write_text(
        '[project]\nname = "p"\n\n[tracing]\nsinks = ["trace_clickhouse:default"]\n'
    )
    (root / "resources.yaml").write_text(
        "trace_clickhouse:\n  default:\n    host: ch.internal\n    media: clickhouse\n"
    )
    (src,) = project_stores(root, env={})
    assert src.spec["media"] == "clickhouse" and "media_dir" not in src.spec
    store = src.open()  # what the studio does
    store._ch = store._given_client = writer_client  # the same server, from another host
    assert isinstance(store.media, ClickHouseMediaStore)
    ref = json.loads(dict(zip(NODE_COLUMNS, writer_client.inserts[1][2][0]))["outputs"])["audio"]
    assert store.media.get(ref["$media"]) == clip
    store.close()


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
    assert live.schema_version() == 4


def test_values_json_cannot_hold_never_sink_a_run(tmp_path):
    from operonx.telemetry.runs.clickhouse import _to_json

    cyclic = {}
    cyclic["self"] = cyclic
    assert json.loads(_to_json(cyclic)) == {"$unserializable": "dict"}
    assert json.loads(_to_json({(1, 2): "tuple key"})) == {"$unserializable": "dict"}
    assert json.loads(_to_json({"big": 2**70})) == {"big": 2**70}  # the stdlib takes over


# -- live: media in ClickHouse ------------------------------------------------------------------


def test_live_media_lands_once_in_the_media_table_and_reads_back(request, tmp_path):
    store = open_store(request, tmp_path, media="clickhouse")
    engine = Operon(voice, params={"text": None}, trace=[store])

    async def go(tid):
        await engine.start(inputs={"text": "xin chào"}, trace_id=tid).collect()

    asyncio.run(go("cm-1"))
    asyncio.run(go("cm-2"))
    rec = store.get_run("cm-1")
    ref = {r["op_name"]: r for r in rec.nodes}["s"]["outputs"]["audio"]
    assert ref["store"] == "clickhouse" and ref["duration_s"] == pytest.approx(1.5, abs=1e-3)
    assert store.media.get(ref["$media"]) == wav(1.5)  # byte-exact, not utf-8 text
    assert store.media.exists(ref["$media"]) and rec.media_root is None
    db = store.database
    (n,) = store._query(f"SELECT count() FROM {db}.media")[0]
    assert n == 1  # two runs, two refs each, one row
    assert not (tmp_path / "media").exists()


def test_live_a_v1_database_upgrades_to_the_current_version(request, tmp_path):
    from operonx.telemetry.runs import clickhouse as chmod

    store = open_store(request, tmp_path, media="clickhouse")
    real = chmod.MIGRATIONS
    chmod.MIGRATIONS = real[:1]
    try:
        v1 = _trace("v1")
        v1.nodes = v1.nodes[1:]  # no media: version 1 had nowhere for it
        # a database at version 1, with a run in it, as a version-1 writer
        # wrote it: without the columns version 4 added
        _, run, nodes, rollups = store.build(v1)
        store._insert("nodes", [n[:-3] for n in nodes], chmod.NODE_COLUMNS[:-3])
        store._insert("op_rollups", rollups, chmod.ROLLUP_COLUMNS)
        store._insert("runs", [run], chmod.RUN_COLUMNS)
        assert store.schema_version() == 1
    finally:
        chmod.MIGRATIONS = real
    store._ready = False  # the next process to open it
    store.put_trace(_with_audio("v2", wav(0.2)))
    assert store.schema_version() == 4  # through v2 to the newest
    assert {s.trace_id for s in store.list_runs().items} == {"v1", "v2"}
    ref = store.get_run("v2").nodes[0]["outputs"]["audio"]
    assert store.media.get(ref["$media"]) == wav(0.2)
    (old,) = store.get_run("v1").nodes
    assert "attempt" not in old and "attrs" not in old and "inputs_from" not in old
    assert old["inputs"] == v1.nodes[0].inputs


def test_live_a_reput_extends_expiry_and_expired_blobs_go(request, tmp_path):
    store = open_store(request, tmp_path, media="clickhouse", ttl_days=None)
    db, clip, old = store.database, wav(0.3), wav(0.6)
    store.put_trace(_with_audio("x-old", old, wall=time.time() - 45 * DAY))  # long expired
    store.put_trace(_with_audio("x-1", clip, wall=time.time() - 25 * DAY))  # 5 days left
    store.put_trace(_with_audio("x-2", clip, wall=time.time() - 1 * DAY))  # extends to ~29
    store._command(f"OPTIMIZE TABLE {db}.media FINAL")
    rows = store._query(f"SELECT sha, expires_at FROM {db}.media FINAL")
    assert len(rows) == 1  # the expired clip is gone, the shared one kept once
    left = rows[0][1].timestamp() - time.time()
    assert 29 * DAY < left < 31 * DAY
    sha = rows[0][0]
    assert store.media.get(sha) == clip


def test_live_prune_media_in_clickhouse_removes_only_orphans(request, tmp_path):
    store = open_store(request, tmp_path, media="clickhouse")
    store.put_trace(_with_audio("pa", wav(0.2)))
    store.put_trace(_with_audio("pb", wav(0.4)))
    assert len(list(store.media.keys())) == 2
    assert store.prune_media(older_than_s=3600) == 0
    store.delete_runs(RunFilter(trace_ids=["pb"]))
    assert store.prune_media(older_than_s=0) == 1
    (kept,) = [sha for sha, _ in store.media.keys()]
    assert store.get_run("pa").nodes[0]["outputs"]["audio"]["$media"] == kept


def test_live_a_v2_database_with_runs_upgrades_to_v3_and_another_host_reads_it(request, tmp_path):
    from operonx.telemetry.runs import clickhouse as chmod
    from operonx.telemetry.scores import Experiment, ExperimentItem, Score, ScoreFilter
    from operonx.telemetry.scores.clickhouse import ClickHouseScoreStore
    from tests.internal.telemetry._clickhouse import clickhouse_spec

    runs = open_store(request, tmp_path)
    real = chmod.MIGRATIONS
    chmod.MIGRATIONS = real[:2]
    try:
        # a 1.14 database, with a run in it, as a 1.14 writer wrote it:
        # without the node columns version 4 added
        _, run, nodes, rollups = runs.build(_trace("before-v3"))
        runs._insert("nodes", [n[:-3] for n in nodes], chmod.NODE_COLUMNS[:-3])
        runs._insert("op_rollups", rollups, chmod.ROLLUP_COLUMNS)
        runs._insert("runs", [run], chmod.RUN_COLUMNS)
        assert runs.schema_version() == 2
    finally:
        chmod.MIGRATIONS = real

    host_a = ClickHouseScoreStore(database=runs.database, **clickhouse_spec())
    exp = Experiment(
        "e1", "labels", project="demo", started_at=time.time(), metrics={"pass": {"mean": 1.0}}
    )
    host_a.put_experiment(exp)
    host_a.put_items([ExperimentItem("e1", "a", output={"label": "x"})])
    host_a.put_scores([Score("exact", experiment_id="e1", case_id="a", passed=True)])
    assert host_a.schema_version() == 4
    assert runs.get_run("before-v3") is not None  # the run is still there

    host_b = ClickHouseScoreStore(database=runs.database, **clickhouse_spec())  # its own client
    try:
        got = host_b.get_experiment("e1")
        assert got.experiment == exp and got.items[0].output == {"label": "x"}
        assert [s.passed for s in host_b.scores(ScoreFilter(experiment_id="e1"))] == [True]
    finally:
        host_a.close()
        host_b.close()
