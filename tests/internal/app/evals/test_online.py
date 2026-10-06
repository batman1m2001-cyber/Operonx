"""Online eval (EVALS_PLAN §14 D65–D71): judge stored runs after the fact.

The runs judged are a real job's, traced into a SQLite run store — the
same rows a service writes — and the scores go to a real score store.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from operonx import END, START, graph, op
from operonx.app.evals.online import Cursor, OnlineEval, RunStoreSource, sampled
from operonx.app.evals.queues import queue_items
from operonx.app.jobs import Job
from operonx.app.serve import egress, ingress
from operonx.telemetry.runs.model import RunFilter
from operonx.telemetry.runs.sqlite import SqliteRunStore
from operonx.telemetry.scores import ScoreFilter, open_score_store


@op
def answer(text: str) -> dict:
    return {"reply": "sorry" if "refund" in text else f"done: {text}"}


@graph
def bot(text):
    a = answer(text=text)
    START >> a >> END


def polite(output) -> dict:
    return {"passed": "sorry" not in str(output), "reason": "apologised"}


def short(output) -> bool:
    return len(str(output)) < 40


@pytest.fixture
def runs(tmp_path):
    return SqliteRunStore(path=tmp_path / "runs.sqlite")


@pytest.fixture
def scores(tmp_path):
    return open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})


def _serve(runs, tmp_path, texts, kind=Job, name="bot"):
    """The traffic: a job's runs, traced into the run store."""
    items = [{"id": f"t{i}", "text": t} for i, t in enumerate(texts)]
    job = kind(
        name,
        graph=bot,
        source=items,
        key="id",
        item_input="text",
        trace=[runs],
        record_dir=tmp_path / "jobs",
    )
    # the item is bound whole to `text`; the graph reads its text
    job.item_of = lambda raw: raw["text"]
    asyncio.run(job.run())
    return [s.trace_id for s in runs.list_runs(RunFilter(name=name), limit=1000).items]


def _online(runs, scores, tmp_path, **kw):
    kw.setdefault("evaluators", [polite, short])
    return OnlineEval(
        "quality",
        runs={"origin": "job", "name": "bot"},
        store=runs,
        scores=scores,
        record_dir=tmp_path / "online",
        queues_dir=tmp_path / "queues",
        **kw,
    )


# ── sampling ─────────────────────────────────────────────────────────────


def test_sampling_is_stable_and_near_its_rate():
    ids = [f"trace-{i}" for i in range(10_000)]
    first = {t for t in ids if sampled(t, 0.05)}
    random.shuffle(ids)
    again = {t for t in ids if sampled(t, 0.05)}

    assert first == again
    # binomial: 500 expected, σ ≈ 21.8 — five sigma either side
    assert 390 < len(first) < 610
    assert {t for t in ids if sampled(t, 0.05, salt="q")} != first
    assert all(sampled(t, 1.0) for t in ids[:50]) and not any(sampled(t, 0.0) for t in ids[:50])


# ── a pass ───────────────────────────────────────────────────────────────


def test_a_pass_judges_every_run_and_writes_scores_with_the_rule(runs, scores, tmp_path):
    ids = _serve(runs, tmp_path, ["hello", "refund please", "status"])
    run = asyncio.run(_online(runs, scores, tmp_path).run())

    assert run.status == "ok"
    online = run.meta["online"]
    assert online["checks"]["polite"] == {"passed": 2, "failed": 1, "errors": 0}
    got = scores.scores(ScoreFilter(rule="quality"))
    assert len(got) == 6  # 3 runs × 2 checks
    assert {s.trace_id for s in got} == set(ids)
    assert {s.origin for s in got} == {"job"} and {s.name for s in got} == {"bot"}
    failed = [s for s in got if s.score_name == "polite" and not s.passed]
    assert failed[0].snapshot["output"] == {"reply": "sorry"}
    assert failed[0].snapshot["input"] == {"text": "refund please"}


def test_the_next_pass_judges_only_new_runs(runs, scores, tmp_path):
    _serve(runs, tmp_path, ["a", "b"])
    asyncio.run(_online(runs, scores, tmp_path).run())
    _serve(runs, tmp_path, ["c"])
    second = asyncio.run(_online(runs, scores, tmp_path).run())

    assert second.counts.get("ok") == 1
    assert len(scores.scores(ScoreFilter(rule="quality"))) == 6


def test_a_rerun_writes_the_same_rows(runs, scores, tmp_path):
    _serve(runs, tmp_path, ["a", "b"])
    asyncio.run(_online(runs, scores, tmp_path).run())
    (tmp_path / "online" / "quality" / "cursor.json").unlink()  # lost: re-read everything
    asyncio.run(_online(runs, scores, tmp_path).run())

    assert len(scores.scores(ScoreFilter(rule="quality"))) == 4


def test_runs_sharing_a_timestamp_across_a_page_are_not_lost(runs, scores, tmp_path):
    from operonx.telemetry.runs.model import RunSummary

    class Fake:
        def __init__(self, summaries):
            self.summaries = summaries

        def list_runs(self, where, order, limit, cursor):
            from operonx.telemetry.runs.model import Page

            start = int(cursor or 0)
            rows = [s for s in self.summaries if s.started_at >= (where.since or 0)]
            page = rows[start : start + limit]
            nxt = str(start + limit) if start + limit < len(rows) else None
            return Page(page, nxt)

    same = [RunSummary(trace_id=f"r{i}", started_at=100.0, name="bot") for i in range(5)]
    store = Fake(same)

    async def take(src):
        return [s.trace_id async for s in src.items()]

    src = RunStoreSource(store, page=2)
    assert asyncio.run(take(src)) == [f"r{i}" for i in range(5)]
    assert src.cursor.started_at == 100.0 and len(src.cursor.trace_ids) == 5

    store.summaries = [*same, RunSummary(trace_id="late", started_at=100.0, name="bot")]
    again = RunStoreSource(store, cursor=src.cursor, page=2)
    assert asyncio.run(take(again)) == ["late"]


def test_a_running_run_holds_the_cursor():
    from operonx.telemetry.runs.model import Page, RunSummary

    rows = [
        RunSummary(trace_id="done1", started_at=1.0),
        RunSummary(trace_id="live", started_at=2.0, status="running"),
        RunSummary(trace_id="done2", started_at=3.0),
    ]

    class Store:
        def list_runs(self, where, order, limit, cursor):
            return Page([r for r in rows if r.started_at >= (where.since or 0)])

    async def take(src):
        return [s.trace_id async for s in src.items()]

    src = RunStoreSource(Store())
    assert asyncio.run(take(src)) == ["done1"]
    rows[1] = RunSummary(trace_id="live", started_at=2.0)
    assert asyncio.run(take(RunStoreSource(Store(), cursor=src.cursor))) == ["live", "done2"]


class _EvalLike(Job):
    origin = "eval"  # an experiment's or a judge's run


def test_eval_runs_are_never_judged(runs, scores, tmp_path):
    (served,) = _serve(runs, tmp_path, ["a"])
    _serve(runs, tmp_path, ["b"], kind=_EvalLike, name="exp")
    online = OnlineEval(
        "everything",
        runs={},
        store=runs,
        evaluators=[short],
        scores=scores,
        record_dir=tmp_path / "online",
    )
    first = asyncio.run(online.run())

    assert [i.key for i in first.items] == [served]
    assert asyncio.run(online.run()).counts.get("ok", 0) == 0


def test_an_evaluator_wanting_an_expected_answer_is_refused(runs, scores, tmp_path):
    def matches(output, expected):
        return output == expected

    with pytest.raises(ValueError, match="no expected answer"):
        _online(runs, scores, tmp_path, evaluators=[matches])


def test_backfill_leaves_the_cursor_alone(runs, scores, tmp_path):
    _serve(runs, tmp_path, ["a", "b"])
    online = _online(runs, scores, tmp_path)
    asyncio.run(online.backfill(since=0).run())

    assert not online.cursor_path.exists()
    assert len(scores.scores(ScoreFilter(rule="quality"))) == 4
    # the live pass still starts from the beginning, and writes the same rows
    asyncio.run(online.run())
    assert len(scores.scores(ScoreFilter(rule="quality"))) == 4
    assert json.loads(online.cursor_path.read_text())["started_at"] > 0


def test_sample_judges_only_the_sampled_runs(runs, scores, tmp_path):
    ids = _serve(runs, tmp_path, [f"q{i}" for i in range(20)])
    run = asyncio.run(_online(runs, scores, tmp_path, sample=0.3).run())

    kept = {t for t in ids if sampled(t, 0.3)}
    assert {s.trace_id for s in scores.scores(ScoreFilter(rule="quality"))} == kept
    assert run.meta["online"]["unsampled"] == 20 - len(kept)


# ── budget ───────────────────────────────────────────────────────────────


def _priced_judge(cost, seconds=0.0):
    async def paid(output) -> dict:
        await asyncio.sleep(seconds)
        return {"passed": True, "cost_usd": cost}

    paid.eval_kind = "judge"
    paid.eval_name = "paid"
    paid.__name__ = "paid"
    return paid


def test_the_budget_stops_judges_and_code_checks_still_run(runs, scores, tmp_path):
    _serve(runs, tmp_path, [f"q{i}" for i in range(6)])
    online = _online(
        runs, scores, tmp_path, evaluators=[short, _priced_judge(0.4)], budget_usd_per_day=1.0
    )
    run = asyncio.run(online.run())

    got = scores.scores(ScoreFilter(rule="quality"))
    assert len([s for s in got if s.score_name == "short"]) == 6
    assert len([s for s in got if s.score_name == "paid"]) == 2  # a third would make 1.2
    assert run.meta["online"]["budget_exhausted"] == 4
    # a second pass the same day starts from what the store says was spent
    _serve(runs, tmp_path, ["late"])
    again = asyncio.run(online.run())
    assert again.meta["online"]["budget_exhausted"] == 1


def test_judges_running_at_once_do_not_overrun_the_budget(runs, scores, tmp_path):
    """The gate on 121 recorded calls spent $0.12 of a $0.05 budget: each
    judge looked at what had been spent when it started, and the ones
    running at once all saw the same total. A judge in flight now counts at
    the day's cost per run, and the first waits for a price to be known."""
    _serve(runs, tmp_path, [f"q{i}" for i in range(12)])
    online = _online(
        runs,
        scores,
        tmp_path,
        evaluators=[_priced_judge(0.4, seconds=0.05)],
        budget_usd_per_day=1.0,
        concurrency=4,
    )
    run = asyncio.run(online.run())

    spent = sum(s.cost_usd or 0 for s in scores.scores(ScoreFilter(rule="quality")))
    assert spent <= 1.0
    assert run.meta["online"]["spent_usd_today"] <= 1.0
    assert run.meta["online"]["budget_exhausted"] == 10


# ── targets and queues ───────────────────────────────────────────────────


def test_failing_runs_go_to_the_queue(runs, scores, tmp_path):
    _serve(runs, tmp_path, ["hello", "refund please", "another refund"])
    online = _online(runs, scores, tmp_path, queue={"to": "rude", "when": "any_failed"})
    run = asyncio.run(online.run())

    items = queue_items(tmp_path / "queues", "rude")
    assert len(items) == 2 and run.meta["online"]["queued"] == 2
    assert all(i.source == "online:quality" and "polite" in i.reason for i in items)


def test_a_session_target_scores_the_runs_session(runs, scores, tmp_path):
    (trace_id,) = _serve(runs, tmp_path, ["a"])
    asyncio.run(_online(runs, scores, tmp_path, target="session").run())

    session = runs.get_run(trace_id).meta["metadata"]["session_id"]
    got = scores.scores(ScoreFilter(rule="quality"))
    assert {(s.target, s.session_id) for s in got} == {("session", session)}


def test_scores_store_is_required(runs, tmp_path):
    with pytest.raises(ValueError, match="needs scores="):
        OnlineEval("x", runs={}, store=runs, evaluators=[short], scores=None)


def test_cursor_round_trips(tmp_path):
    c = Cursor(5.0, ["a", "b"])
    c.save(tmp_path / "c.json")
    assert Cursor.load(tmp_path / "c.json") == c
    assert Cursor.load(tmp_path / "missing.json") == Cursor()


# ── never inline (D76) ───────────────────────────────────────────────────


@graph
def door():
    src = ingress()
    a = answer(text=src["item"])
    out = egress(item=a["reply"])
    START >> src >> a >> out >> END


def test_a_served_request_runs_no_evaluator(runs, scores, tmp_path):
    """The service writes its run and answers; the evaluator runs only when
    the online pass reads the run later."""
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from operonx import Operon
    from operonx.app.manifest import ServeSpec
    from operonx.app.serve.app import build_app

    calls = []

    def watched(output) -> bool:
        calls.append(output)
        return True

    online = OnlineEval(
        "watch",
        runs={"origin": "service"},
        store=runs,
        evaluators=[watched],
        scores=scores,
        record_dir=tmp_path / "online",
    )
    spec = ServeSpec(name="chat", kind="http", graph="x:y", path="/chat", method="POST")
    app = build_app((spec,), engines={"chat": Operon(door, trace=[runs])})
    with TestClient(app) as client:
        assert client.post("/chat", json="hello").status_code == 200

    assert calls == []
    asyncio.run(online.run())
    assert len(calls) == 1


def test_a_judge_that_only_uses_a_reference_when_there_is_one_is_accepted(runs, scores, tmp_path):
    """`judge(...)` defaults to reference="auto": it shows `expected` when a
    case has one. Online there is none, so it judges without — refusing it
    refused every default judge."""
    from operonx.app.evals import judge

    def lenient(output, expected=None) -> bool:
        return expected is None

    _online(
        runs,
        scores,
        tmp_path,
        evaluators=[judge("llm:j", "Was it polite?", name="polite"), lenient],
    )
    with pytest.raises(ValueError, match="no expected answer"):
        _online(
            runs, scores, tmp_path, evaluators=[judge("llm:j", "Polite?", name="p", reference=True)]
        )
