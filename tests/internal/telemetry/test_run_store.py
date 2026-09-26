"""Run stores — P1 of the platform plan.

The gates:

* one contract, every backend: the same tests run against ``files`` and
  ``sqlite`` — write through the engine, list, filter, page, read one
  run fully (media included), roll ops up, delete, apply retention;
* the numbers are the raw records' numbers: every summary and rollup is
  recomputed here from the rows and compared;
* cost follows operonx's rule — a priced zero is a zero, ``None`` is
  *unpriced*, and a run's cost is ``None`` only when nothing was priced;
* the files store indexes run directories other consumers wrote, old
  flat ones included, and forgets directories that are gone;
* Langfuse reads back as the same rows.
"""

from __future__ import annotations

import asyncio
import textwrap
import time
from pathlib import Path

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.registry import ResourceHub
from operonx.core.workflow_trace import STATUS_ERROR, STATUS_OK, OpExecution, WorkflowTrace
from operonx.telemetry.consumers.local import LocalConsumer
from operonx.telemetry.runs import (
    DEFAULT_RETENTION,
    RunFilter,
    apply_retention,
    combine_rollups,
    open_run_store,
    percentile,
    summarize,
)
from operonx.telemetry.runs.files import FilesRunStore
from operonx.telemetry.runs.langfuse import LangfuseRunStore, records_of_langfuse_trace
from operonx.telemetry.runs.model import MAX_SAMPLES
from operonx.telemetry.runs.retention import plan_retention
from operonx.telemetry.runs.sqlite import SqliteRunStore

# -- building traces by hand, so every number is known -----------------------------

DAY = 86400.0
NOW = 1790467200.0  # 2026-09-27 00:00 UTC


def _exec(
    op, start, ms, *, status=STATUS_OK, error=None, outputs=None, ctx=("main",), op_type="code"
):
    return OpExecution(
        op_id=f"g.{op}#{'.'.join(ctx)}@{start}",
        op_name=op,
        op_full_name=f"g.{op}",
        ctx=ctx,
        start_time=start,
        end_time=start + ms / 1000.0,
        inputs={"x": 1},
        outputs=outputs or {},
        upstreams=[],
        status=status,
        error=error,
        op_type=op_type,
    )


def _trace(trace_id, *, wall, nodes, **meta):
    t = WorkflowTrace(
        trace_id=trace_id,
        workflow_name="flow",
        started_at=100.0,
        ended_at=100.0 + max((n.end_time for n in nodes), default=100.0) - 100.0,
        nodes=nodes,
        metadata=meta,
        wall_started_at=wall,
    )
    return t


def _llm(start, ms, cost, tokens=(10, 5, 2)):
    return _exec(
        "reply",
        start,
        ms,
        op_type="llm",
        outputs={
            "content": "hi",
            "cost_usd": cost,
            "usage": {
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "cached_tokens": tokens[2],
            },
        },
    )


def _calls():
    """Three service runs and a job run over three days."""
    return [
        _trace(
            "call-1",
            wall=NOW - 2 * DAY,
            origin="service",
            service="call",
            session_id="0912",
            nodes=[_exec("stt", 100.0, 40), _llm(100.1, 300, 0.002), _exec("tts", 100.5, 80)],
        ),
        _trace(
            "call-2",
            wall=NOW - 1 * DAY,
            origin="service",
            service="call",
            session_id="0999",
            nodes=[
                _exec("stt", 100.0, 60),
                _llm(100.1, 500, None),
                _exec(
                    "tts", 100.7, 120, status=STATUS_ERROR, error="Traceback…\nTTSError: timeout"
                ),
            ],
        ),
        _trace(
            "call-3",
            wall=NOW,
            origin="service",
            service="call",
            nodes=[_exec("stt", 100.0, 50), _llm(100.1, 100, 0.0), _exec("tts", 100.3, 90)],
        ),
        _trace(
            "item-a",
            wall=NOW - 40 * DAY,
            origin="job",
            job="qc_cases",
            job_run="R1",
            key="educa-002",
            nodes=[_exec("check", 100.0, 20)],
        ),
    ]


# -- summarize: the numbers ---------------------------------------------------------


def test_summaries_count_time_errors_cost_and_tokens():
    rows_of = {t.trace_id: t for t in _calls()}
    s, rolls = summarize(
        "call-2",
        [_row(n) for n in rows_of["call-2"].nodes],
        {"wall_started_at": NOW, "metadata": rows_of["call-2"].metadata},
    )
    assert (s.origin, s.name, s.status, s.errors) == ("service", "call", "error", 1)
    assert s.first_error == "tts: TTSError: timeout"  # the last line, not the traceback head
    assert s.executions == 3 and s.ops == 3
    assert s.cost_usd is None and s.unpriced == 1 and s.llm_calls == 1  # nothing priced
    assert (s.tokens_in, s.tokens_out, s.tokens_cached) == (10, 5, 2)
    by = {r.op: r for r in rolls}
    assert by["tts"].errors == 1 and by["reply"].unpriced == 1 and by["reply"].cost_usd is None


def test_a_declared_zero_is_a_price_and_unpriced_is_not():
    t = _trace("z", wall=NOW, nodes=[_llm(100.0, 10, 0.0), _llm(100.1, 10, None)])
    s, _ = summarize("z", [_row(n) for n in t.nodes], {"metadata": {}})
    assert s.cost_usd == 0.0 and s.unpriced == 1  # "$0 + 1 unpriced", never "$0"


def test_samples_keep_every_duration_up_to_the_cap_then_thin_evenly():
    many = [_exec("tick", 100.0 + i / 1000, float(i)) for i in range(MAX_SAMPLES * 3)]
    _, rolls = summarize("m", [_row(n) for n in many], {})
    (r,) = rolls
    assert r.count == MAX_SAMPLES * 3 and not r.exact and len(r.samples) == MAX_SAMPLES
    assert r.samples[0] == pytest.approx(0.0) and r.samples[-1] == pytest.approx(
        MAX_SAMPLES * 3 - 1
    )
    few = [_exec("tick", 100.0, float(i)) for i in range(5)]
    _, (r2,) = summarize("f", [_row(n) for n in few], {})
    assert r2.exact and sorted(r2.samples) == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0])


def test_percentile_is_linear_between_ranks():
    assert percentile([], 95) == 0.0
    assert percentile([7], 50) == 7.0
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert percentile([0, 10], 95) == pytest.approx(9.5)


def _row(n: OpExecution) -> dict:
    return {
        "op_name": n.op_name,
        "op_full_name": n.op_full_name,
        "op_type": n.op_type,
        "start_time": n.start_time,
        "end_time": n.end_time,
        "duration_ms": n.duration_ms,
        "status": n.status,
        "error": n.error,
        "outputs": n.outputs,
    }


# -- the contract, per backend --------------------------------------------------------


@pytest.fixture(params=["files", "sqlite"])
def store(request, tmp_path):
    if request.param == "files":
        s = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    else:
        s = SqliteRunStore(path=tmp_path / "runs.sqlite")
    for t in _calls():
        s.consume(t)
    return s


def test_list_runs_newest_first_with_the_summary_fields(store):
    page = store.list_runs()
    assert [s.trace_id for s in page.items] == ["call-3", "call-2", "call-1", "item-a"]
    assert page.total == 4 and page.next_cursor is None
    c2 = page.items[1]
    assert c2.origin == "service" and c2.name == "call" and c2.status == "error"
    assert c2.session_id == "0999" and c2.metadata["session_id"] == "0999"


@pytest.mark.parametrize(
    "where, expected",
    [
        (RunFilter(origin="job"), ["item-a"]),
        (RunFilter(origin="service", name="call", status="error"), ["call-2"]),
        (RunFilter(since=NOW - DAY), ["call-3", "call-2"]),
        (RunFilter(until=NOW - DAY), ["call-1", "item-a"]),
        (RunFilter(metadata={"session_id": "0912"}), ["call-1"]),
        (RunFilter(search="EDUCA-002"), ["item-a"]),
        (RunFilter(job_run="R1"), ["item-a"]),
        (RunFilter(trace_ids=["call-1", "item-a"]), ["call-1", "item-a"]),
        (RunFilter(trace_ids=[]), []),
    ],
)
def test_filters(store, where, expected):
    assert [s.trace_id for s in store.list_runs(where).items] == expected


def test_orders_and_pages(store):
    by_cost = [s.trace_id for s in store.list_runs(order="cost_desc").items]
    assert by_cost[0] == "call-1" and by_cost[1] == "call-3"  # $0.002, then a declared $0
    slow = [
        s.trace_id
        for s in store.list_runs(RunFilter(origin="service"), order="duration_desc").items
    ]
    assert slow[0] == "call-2"
    first = store.list_runs(limit=3)
    assert len(first.items) == 3 and first.next_cursor
    rest = store.list_runs(limit=3, cursor=first.next_cursor)
    assert [s.trace_id for s in rest.items] == ["item-a"] and rest.next_cursor is None
    with pytest.raises(ValueError):
        store.list_runs(order="loudest")


def test_get_run_returns_every_row_with_its_values(store):
    rec = store.get_run("call-2")
    assert rec.summary.trace_id == "call-2" and len(rec.nodes) == 3
    assert rec.nodes[0]["inputs"] == {"x": 1}
    assert rec.meta["metadata"]["service"] == "call"
    assert store.get_run("nope") is None


def test_rollups_match_the_raw_rows(store):
    stats = {s.op: s for s in store.op_stats(RunFilter(origin="service"))}
    stt_ms = [40.0, 60.0, 50.0]
    assert stats["stt"].runs == 3 and stats["stt"].count == 3
    assert stats["stt"].total_ms == pytest.approx(sum(stt_ms))
    assert stats["stt"].max_ms == pytest.approx(60.0)
    assert stats["stt"].p50_ms == pytest.approx(percentile(stt_ms, 50))
    assert stats["stt"].p95_ms == pytest.approx(percentile(stt_ms, 95))
    assert stats["reply"].cost_usd == pytest.approx(0.002) and stats["reply"].unpriced == 1
    assert stats["tts"].errors == 1
    # slowest total first
    assert list(stats)[0] == "reply"


def test_delete_and_retention(store):
    counted = plan_retention(store, now=NOW + 60)
    assert counted["service"] == 0 and counted["adhoc"] == 0 and "job" not in counted  # forever
    assert plan_retention(store, {"job": 30}, now=NOW) == {"job": 1}
    assert apply_retention(store, {"service": 1.5, "job": None}, now=NOW + 60) == {"service": 1}
    assert [s.trace_id for s in store.list_runs().items] == ["call-3", "call-2", "item-a"]
    assert store.delete_runs(RunFilter(trace_ids=["call-3"])) == 1
    assert store.get_run("call-3") is None and store.count() == 2
    with pytest.raises(ValueError):
        apply_retention(store, {"service": -1}, now=NOW)
    assert DEFAULT_RETENTION == {
        "service": 30,
        "job": None,
        "eval": None,
        "playground": 7,
        "adhoc": 30,
    }


# -- through the engine and the hub ------------------------------------------------------


@op(bound="io")
async def upper(text: str = "") -> dict:
    return {"out": text.upper(), "blob": b"\x00" * 4096}


@graph
def shout(text):
    step = upper(text=text)
    step["out"] >> PARENT["out"]
    step["blob"] >> PARENT["blob"]
    START >> step >> END


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_an_engine_run_lands_in_the_store_with_its_media(tmp_path, backend):
    store = open_run_store(
        {"backend": backend, "root": str(tmp_path / "runs"), "path": str(tmp_path / "r.sqlite")}
    )
    engine = Operon(shout, params={"text": None}, trace=[store])

    async def go():
        handle = engine.start(inputs={"text": "hi"}, trace_id="e-1")
        await handle.collect()

    asyncio.run(go())
    rec = store.get_run("e-1")
    (row,) = [r for r in rec.nodes if r["op_name"] == "step"]
    assert row["outputs"]["out"] == "HI"
    ref = row["outputs"]["blob"]["$media_ref"]
    assert (Path(rec.media_root) / ref).stat().st_size == 4096  # the media resolves


def test_a_store_is_a_resource_and_a_trace_consumer(tmp_path):
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        textwrap.dedent(f"""
        run_store:
          default:
            backend: sqlite
            path: {tmp_path / "hub.sqlite"}
        """)
    )
    import operonx.telemetry  # noqa: F401 — registers run_store:

    ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))
    engine = Operon(shout, params={"text": None}, trace="run_store:default")

    async def go():
        await engine.start(inputs={"text": "x"}, trace_id="hub-1").collect()

    asyncio.run(go())
    store = ResourceHub.instance().get("run_store:default")
    assert [s.trace_id for s in store.list_runs().items] == ["hub-1"]


# -- files: what other writers left ---------------------------------------------------------


def test_files_indexes_directories_a_plain_consumer_wrote_flat_or_by_origin(tmp_path):
    root = tmp_path / "runs"
    flat, by_origin = (
        LocalConsumer(config={"root": root, "layout": "flat"}),
        LocalConsumer(config={"root": root}),
    )
    t1, t2 = _calls()[0], _calls()[3]
    flat.consume(t1)
    by_origin.consume(t2)
    store = FilesRunStore(root=root, refresh_every=0)
    got = {s.trace_id: s for s in store.list_runs().items}
    assert set(got) == {"call-1", "item-a"}
    assert (
        got["call-1"].location == "call-1" and got["item-a"].location == "jobs/qc_cases/R1/item-a"
    )
    # a directory removed behind the store's back is forgotten on the next look
    import shutil

    shutil.rmtree(root / "call-1")
    assert [s.trace_id for s in store.list_runs().items] == ["item-a"]


def test_files_delete_prunes_the_empty_day_folder(tmp_path):
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    store.consume(_calls()[0])
    day = tmp_path / "runs" / "services" / "call"
    assert any(day.iterdir())
    store.delete_runs(RunFilter(trace_ids=["call-1"]))
    assert not day.exists()


def test_files_refresh_is_throttled(tmp_path):
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=60)
    LocalConsumer(config={"root": tmp_path / "runs"}).consume(_calls()[0])
    store._last_refresh = time.monotonic()
    assert store.list_runs().items == []  # within the window: no walk
    assert store.refresh() == 1 and len(store.list_runs().items) == 1


def test_combine_rollups_counts_runs_not_rows():
    t = _calls()[0]
    _, a = summarize("a", [_row(n) for n in t.nodes], {})
    _, b = summarize("b", [_row(n) for n in t.nodes], {})
    stt = {s.op: s for s in combine_rollups(a + b)}["stt"]
    assert stt.runs == 2 and stt.count == 2 and stt.exact


# -- Langfuse reads back as the same rows ------------------------------------------------


DETAIL = {
    "id": "lf-1",
    "name": "engine",
    "timestamp": "2026-09-27T00:00:00Z",
    "tags": ["origin:service", "service:call"],
    "metadata": {"session_id": "0912"},
    "observations": [
        {
            "id": "o1",
            "name": "stt",
            "startTime": "2026-09-27T00:00:00.000Z",
            "endTime": "2026-09-27T00:00:00.040Z",
            "metadata": {
                "op_full_name": "engine.stt",
                "ctx": "main",
                "status": "ok",
                "duration_ms": 40,
            },
            "input": {"a": 1},
            "output": {"text": "xin chao"},
        },
        {
            "id": "o2",
            "name": "reply",
            "startTime": "2026-09-27T00:00:00.100Z",
            "endTime": "2026-09-27T00:00:00.400Z",
            "level": "ERROR",
            "statusMessage": "boom",
            "metadata": {"op_full_name": "engine.reply", "op_type": "llm"},
            "output": {"cost_usd": 0.001, "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
        },
    ],
}


def test_langfuse_observations_become_rows():
    rows = list(records_of_langfuse_trace(DETAIL))
    assert [r["op_name"] for r in rows] == ["stt", "reply"]
    assert rows[0]["duration_ms"] == 40 and rows[0]["outputs"] == {"text": "xin chao"}
    assert rows[1]["status"] == "error" and rows[1]["error"] == "boom"
    assert rows[1]["duration_ms"] == pytest.approx(300.0)


def test_langfuse_store_lists_filters_and_reads(monkeypatch):
    store = LangfuseRunStore("https://lf.example", "pk", "sk")
    listing = {
        "data": [
            DETAIL | {"latency": 0.4, "totalCost": 0.001},
            {
                "id": "lf-2",
                "name": "engine",
                "timestamp": "2026-09-26T00:00:00Z",
                "tags": ["origin:job", "job:qc"],
            },
        ],
        "meta": {"totalPages": 1},
    }
    calls = []

    def fake(path, **params):
        calls.append((path, params))
        return listing if path == "/api/public/traces" else DETAIL

    monkeypatch.setattr(store, "_get", fake)
    page = store.list_runs(RunFilter(origin="service"))
    assert [s.trace_id for s in page.items] == ["lf-1"]
    s = page.items[0]
    assert (
        s.name == "call"
        and s.duration_ms == pytest.approx(400.0)
        and s.cost_usd == pytest.approx(0.001)
    )
    rec = store.get_run("lf-1")
    assert rec.summary.errors == 1 and rec.summary.tokens_in == 3 and len(rec.nodes) == 2
    assert store.delete_runs(RunFilter()) == 0 and not store.writable
    with pytest.raises(NotImplementedError):
        store.put_trace(object())
