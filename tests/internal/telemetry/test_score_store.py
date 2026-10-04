"""Score stores — P3 of the eval track (EVALS_PLAN D30–D33).

The gates:

* one contract, every backend: the same tests run against ``files``,
  ``sqlite`` and — when a throwaway server is configured (see
  ``_clickhouse.py``) — ``clickhouse``: experiments upsert and list, filter
  and page; items and scores re-put collapse; a human's edit replaces their
  earlier value even across months; series and the judge cache; an online
  score past its retention is gone;
* score ids follow T4 §6.3, and each target says which id it lacks;
* the files store's JSONL is the truth: its index rebuilds from the files
  and picks up lines another writer appended;
* ClickHouse schema v3 is a migration on the run store's chain: a v2
  database gets exactly the four new tables and no ``CREATE DATABASE``;
  live, a v2 database with a run in it upgrades and keeps the run, and an
  experiment written through one client reads back through another.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

from operonx.telemetry.scores import (
    Experiment,
    ExperimentFilter,
    ExperimentItem,
    Score,
    ScoreFilter,
    open_score_store,
)
from operonx.telemetry.scores.files import FilesScoreStore
from operonx.telemetry.scores.sqlite import SqliteScoreStore

DAY = 86400.0
NOW = time.time()


# -- fixtures ------------------------------------------------------------------------


def _clickhouse_score_store(request, tmp_path, **kw):
    from tests.internal.telemetry._clickhouse import clickhouse_spec, why_skip

    spec = clickhouse_spec()
    if spec is None:
        pytest.skip(why_skip())
    from operonx.telemetry.scores.clickhouse import ClickHouseScoreStore

    database = f"t_{uuid.uuid4().hex[:10]}"
    store = ClickHouseScoreStore(database=database, **spec, **kw)

    def drop():
        try:
            store._command(f"DROP DATABASE IF EXISTS {database}")
        finally:
            store.close()

    request.addfinalizer(drop)
    return store


@pytest.fixture(params=["files", "sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "files":
        return FilesScoreStore(root=tmp_path / "scores", refresh_every=0)
    if request.param == "sqlite":
        return SqliteScoreStore(path=tmp_path / "scores.sqlite")
    return _clickhouse_score_store(request, tmp_path)


def _exp(i, **kw):
    base = dict(
        experiment_id=f"exp-{i}",
        eval="replies" if i % 2 else "labels",
        project="demo",
        dataset="replies",
        dataset_version="abc123",
        graph="flow",
        graph_hash="g1",
        config_hash="c1",
        evaluators_hash="e1",
        operonx_version="1.14.0",
        code_version=f"sha{i}",
        version_dirty=False,
        repeats=1,
        status="ok",
        started_at=NOW - (10 - i) * 3600,
        ended_at=NOW - (10 - i) * 3600 + 60,
        cases=3,
        errored=0,
        cost_usd=0.01 * i,
        p50_ms=12.5,
        metrics={"pass": {"n": 3, "mean": 0.66, "ci_lo": 0.2, "ci_hi": 0.9}},
        gate={"verdict": "pass", "exit_code": 0, "reasons": []},
        metadata={"pass_rate": 0.66},
    )
    base.update(kw)
    return Experiment(**base)


def _item(exp, case, repeat=0, **kw):
    base = dict(
        experiment_id=exp,
        case_id=case,
        repeat=repeat,
        case_hash="h-" + case,
        trace_id=f"{exp}/{case}/{repeat}",
        status="ok",
        ms=11.0,
        cost_usd=0.001,
        tokens_in=12,
        tokens_out=4,
        passed=True,
        tags=["refund"],
        cluster=None,
        output={"label": "refund", "n": [1, 2]},
        error=None,
    )
    base.update(kw)
    return ExperimentItem(**base)


def _score(exp, case, name="exact(label)", repeat=0, *, created_at=None, **kw):
    base = dict(
        score_name=name,
        target="item",
        source="code",
        data_type="bool",
        passed=True,
        experiment_id=exp,
        case_id=case,
        repeat=repeat,
        trace_id=f"{exp}/{case}/{repeat}",
        origin="eval",
        name="replies",
        evaluator_version="v1",
        created_at=created_at or NOW - 3600,
    )
    base.update(kw)
    return Score(**base)


def _settle(store):
    """ClickHouse merges on its own time; reads use FINAL / dedupe."""
    return store


# -- experiments ---------------------------------------------------------------------


def test_an_experiment_reads_back_whole_with_its_items(store):
    exp = _exp(1)
    items = [
        _item("exp-1", "a"),
        _item("exp-1", "b", passed=False, error="boom"),
        _item("exp-1", "a", 1),
    ]
    store.put_experiment(exp)
    store.put_items(items)
    got = store.get_experiment("exp-1")
    assert got.experiment == exp
    assert got.items == sorted(items, key=lambda i: (i.case_id, i.repeat))
    assert store.get_experiment("nope") is None


def test_an_experiment_upserts_running_then_finished(store):
    store.put_experiment(_exp(1, status="running", ended_at=None, metrics={}, gate={}))
    time.sleep(0.01)  # a later write
    store.put_experiment(_exp(1))
    page = store.list_experiments()
    assert [e.experiment_id for e in page.items] == ["exp-1"]
    assert page.items[0].status == "ok" and page.items[0].gate["verdict"] == "pass"


def test_list_experiments_filters_orders_and_pages(store):
    for i in range(1, 6):
        store.put_experiment(_exp(i, status="failed" if i == 3 else "ok"))
    page = store.list_experiments()
    assert [e.experiment_id for e in page.items] == [f"exp-{i}" for i in (5, 4, 3, 2, 1)]
    assert page.total == 5 and page.next_cursor is None
    assert [
        e.experiment_id for e in store.list_experiments(ExperimentFilter(eval="replies")).items
    ] == [
        "exp-5",
        "exp-3",
        "exp-1",
    ]
    assert [
        e.experiment_id for e in store.list_experiments(ExperimentFilter(status="failed")).items
    ] == ["exp-3"]
    since = _exp(3).started_at
    assert [
        e.experiment_id
        for e in store.list_experiments(
            ExperimentFilter(since=since, until=_exp(5).started_at)
        ).items
    ] == ["exp-4", "exp-3"]
    assert [
        e.experiment_id
        for e in store.list_experiments(ExperimentFilter(experiment_ids=["exp-2", "exp-9"])).items
    ] == ["exp-2"]
    assert (
        store.list_experiments(ExperimentFilter(code_version="sha4")).items[0].experiment_id
        == "exp-4"
    )
    first = store.list_experiments(limit=2)
    second = store.list_experiments(limit=2, cursor=first.next_cursor)
    third = store.list_experiments(limit=2, cursor=second.next_cursor)
    assert [e.experiment_id for e in first.items + second.items + third.items] == [
        f"exp-{i}" for i in (5, 4, 3, 2, 1)
    ]
    assert third.next_cursor is None


def test_items_re_put_collapse_and_the_last_write_wins(store):
    store.put_experiment(_exp(1))
    store.put_items([_item("exp-1", "a", passed=False)])
    time.sleep(0.01)
    store.put_items([_item("exp-1", "a", passed=True), _item("exp-1", "a", passed=True)])
    (item,) = store.get_experiment("exp-1").items
    assert item.passed is True


# -- scores --------------------------------------------------------------------------


def test_scores_re_put_collapse(store):
    scores = [_score("exp-1", c) for c in ("a", "b")]
    store.put_scores(scores)
    store.put_scores(scores)  # a retried batch
    store.put_scores([_score("exp-1", "a")])  # the same verdict, built again
    got = store.scores(ScoreFilter(experiment_id="exp-1"))
    assert sorted(s.case_id for s in got) == ["a", "b"]
    assert got[0] == next(s for s in scores if s.case_id == got[0].case_id)


def test_scores_filter_and_order(store):
    store.put_scores(
        [
            _score("exp-1", "a", created_at=NOW - 300),
            _score(
                "exp-1",
                "b",
                name="judge:polite",
                source="judge",
                created_at=NOW - 200,
                cost_usd=0.002,
            ),
            _score("exp-2", "a", created_at=NOW - 100),
            Score(
                score_name="judge:polite",
                target="trace",
                source="judge",
                trace_id="call-9",
                origin="service",
                name="call",
                evaluator_version="v7",
                rule="polite_5pct",
                passed=False,
                reason="curt",
                snapshot={"output": "no."},
                created_at=NOW - 50,
            ),
        ]
    )

    def ids(**kw):
        return [(s.experiment_id, s.case_id, s.trace_id) for s in store.scores(ScoreFilter(**kw))]

    assert ids(experiment_id="exp-1") == [("exp-1", "a", "exp-1/a/0"), ("exp-1", "b", "exp-1/b/0")]
    assert ids(score_name="judge:polite") == [("exp-1", "b", "exp-1/b/0"), (None, None, "call-9")]
    assert ids(source="judge", target="trace") == [(None, None, "call-9")]
    assert ids(origin="service", rule="polite_5pct") == [(None, None, "call-9")]
    assert ids(trace_id="exp-2/a/0") == [("exp-2", "a", "exp-2/a/0")]
    assert ids(since=NOW - 250, until=NOW - 100) == [("exp-1", "b", "exp-1/b/0")]
    online = store.scores(ScoreFilter(trace_id="call-9"))[0]
    assert (online.passed, online.value, online.reason, online.snapshot) == (
        False,
        0.0,
        "curt",
        {"output": "no."},
    )
    assert len(store.scores(limit=2)) == 2


def test_a_human_edit_replaces_the_earlier_value_even_a_month_later(store):
    first = Score(
        score_name="review",
        target="trace",
        source="human",
        author="lan",
        trace_id="call-1",
        data_type="categorical",
        label="bad",
        created_at=NOW - 40 * DAY,
    )
    store.put_scores([first])
    time.sleep(0.01)
    edit = Score(
        score_name="review",
        target="trace",
        source="human",
        author="lan",
        trace_id="call-1",
        data_type="categorical",
        label="good",
        created_at=NOW,
    )
    assert edit.score_id == first.score_id
    store.put_scores([edit])
    (got,) = store.scores(ScoreFilter(trace_id="call-1"))
    assert got.label == "good"
    other = Score(
        score_name="review",
        target="trace",
        source="human",
        author="minh",
        trace_id="call-1",
        label="bad",
        data_type="categorical",
    )
    store.put_scores([other])
    assert sorted(s.author for s in store.scores(ScoreFilter(trace_id="call-1"))) == ["lan", "minh"]


def test_an_online_score_past_its_retention_is_gone_and_others_are_kept(store):
    old = NOW - 400 * DAY
    online = Score(
        score_name="judge:polite",
        target="trace",
        source="judge",
        trace_id="t-old",
        rule="r",
        passed=True,
        created_at=old,
    )
    human = Score(
        score_name="review",
        target="trace",
        source="human",
        author="lan",
        trace_id="t-old",
        passed=True,
        created_at=old,
    )
    offline = _score("exp-old", "a", created_at=old)
    store.put_scores([online, human, offline])
    kept = store.scores()
    assert sorted(s.score_name for s in kept) == ["exact(label)", "review"]


def test_score_series_buckets_by_time_and_name(store):
    hour = 3600.0
    t0 = (NOW // hour) * hour - 5 * hour
    store.put_scores(
        [
            _score("e", "a", created_at=t0 + 10),
            _score("e", "b", created_at=t0 + 20, passed=False),
            _score("e", "c", created_at=t0 + hour + 5),
            _score(
                "e",
                "a",
                name="fuzzy",
                data_type="numeric",
                value=0.5,
                passed=True,
                created_at=t0 + 30,
            ),
            _score(
                "e",
                "b",
                name="fuzzy",
                data_type="numeric",
                value=0.25,
                passed=None,
                created_at=t0 + 40,
            ),
        ]
    )
    got = [
        (b.start, b.score_name, b.n, b.mean, b.passed)
        for b in store.score_series(ScoreFilter(experiment_id="e"), hour)
    ]
    assert got == [
        (t0, "exact(label)", 2, 0.5, 0.5),
        (t0, "fuzzy", 2, 0.375, 1.0),
        (t0 + hour, "exact(label)", 1, 1.0, 1.0),
    ]


def test_the_judge_cache(store):
    assert store.cache_get("k1") is None
    store.cache_put("k1", {"passed": True, "reason": "fine"})
    assert store.cache_get("k1") == {"passed": True, "reason": "fine"}
    time.sleep(0.01)
    store.cache_put("k1", {"passed": False})
    assert store.cache_get("k1") == {"passed": False}
    store.cache_put("k2", {"passed": True}, ttl_s=-1)  # already expired
    assert store.cache_get("k2") is None


# -- ids -------------------------------------------------------------------------------


def test_score_ids_follow_what_was_judged():
    a = _score("exp-1", "a")
    assert (
        a.score_id == _score("exp-1", "a", passed=False, created_at=NOW - 9).score_id
    )  # not the value
    assert a.score_id != _score("exp-1", "a", repeat=1).score_id
    assert a.score_id != _score("exp-2", "a").score_id
    assert (
        a.score_id == _score("exp-1", "a", evaluator_version="v2").score_id
    )  # an item's check is one row

    online = dict(score_name="judge:polite", target="trace", source="judge", trace_id="t1")
    assert (
        Score(**online, evaluator_version="v1").score_id
        != Score(**online, evaluator_version="v2").score_id
    )
    op = Score(score_name="x", target="op", trace_id="t1", op_id="g.a#main")
    assert (
        op.score_id != Score(score_name="x", target="op", trace_id="t1", op_id="g.b#main").score_id
    )

    pair = dict(
        score_name="pref",
        target="pair",
        source="judge",
        experiment_id="A",
        pair_experiment_id="B",
        case_id="c",
    )
    assert Score(**pair, label="A").score_id == Score(**pair, label="B").score_id
    assert Score(**pair).score_id != Score(**{**pair, "pair_experiment_id": "C"}).score_id

    assert a.value == 1.0 and _score("e", "x", passed=False).value == 0.0  # bools average


@pytest.mark.parametrize(
    "fields,missing",
    [
        ({"target": "item", "experiment_id": "e"}, "case_id"),
        ({"target": "trace"}, "trace_id"),
        ({"target": "op", "trace_id": "t"}, "op_id"),
        ({"target": "session"}, "session_id"),
        ({"target": "pair", "experiment_id": "a", "case_id": "c"}, "pair_experiment_id"),
        ({"target": "trace", "trace_id": "t", "source": "human"}, "author"),
    ],
)
def test_each_target_says_which_id_it_lacks(fields, missing):
    with pytest.raises(ValueError, match=missing):
        Score(score_name="s", **fields)


def test_unknown_kinds_are_refused():
    for bad in ({"target": "run"}, {"source": "llm"}, {"data_type": "text"}):
        with pytest.raises(ValueError, match="one of"):
            Score(score_name="s", trace_id="t", **{"target": "trace", **bad})


# -- the files store: the JSONL is the truth ------------------------------------------------


def test_the_files_index_rebuilds_from_the_files(tmp_path):
    root = tmp_path / "scores"
    a = FilesScoreStore(root=root, refresh_every=0)
    a.put_experiment(_exp(1))
    a.put_items([_item("exp-1", "a")])
    a.put_scores([_score("exp-1", "a")])
    (root / ".index.sqlite").unlink()
    for extra in ("-wal", "-shm"):
        (root / f".index.sqlite{extra}").unlink(missing_ok=True)
    b = FilesScoreStore(root=root, refresh_every=0)
    assert b.get_experiment("exp-1").experiment == _exp(1)
    assert len(b.scores()) == 1
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.jsonl")) == [
        "experiments.jsonl",
        "items/exp-1.jsonl",
        f"scores/{time.strftime('%Y-%m', time.gmtime(NOW - 3600))}.jsonl",
    ]


def test_the_files_store_reads_lines_another_writer_appended(tmp_path):
    root = tmp_path / "scores"
    store = FilesScoreStore(root=root, refresh_every=0)
    store.put_experiment(_exp(1))
    other = _exp(2).to_dict()
    with (root / "experiments.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**other, "written_at": time.time()}) + "\n")
        fh.write('{"half a line')  # a writer mid-line: not read until it ends
    assert [e.experiment_id for e in store.list_experiments().items] == ["exp-2", "exp-1"]
    with (root / "experiments.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('"}\n')  # finished, but not an experiment: skipped, not fatal
    assert store.refresh() == 0
    assert len(store.list_experiments().items) == 2


def test_open_score_store_reads_a_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_RUNS_DIR", str(tmp_path / "runs"))
    files = open_score_store({"backend": "files"})
    assert isinstance(files, FilesScoreStore) and files.root == tmp_path / "runs" / "scores"
    sqlite = open_score_store({"backend": "sqlite", "path": str(tmp_path / "s.sqlite")})
    assert isinstance(sqlite, SqliteScoreStore)
    with pytest.raises(ValueError, match="needs host"):
        open_score_store({"backend": "clickhouse"})
    with pytest.raises(ValueError, match="one of files, sqlite, clickhouse"):
        open_score_store({"backend": "mongo"})


def test_a_score_store_is_a_resource(tmp_path, monkeypatch):
    from operonx.core.registry import ResourceHub

    path = tmp_path / "resources.yaml"
    path.write_text(
        f"score_store:\n  team:\n    backend: sqlite\n    path: {tmp_path / 't.sqlite'}\n",
        encoding="utf-8",
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
    try:
        assert isinstance(ResourceHub.instance().get("score_store:team"), SqliteScoreStore)
    finally:
        ResourceHub.reset_instance()
