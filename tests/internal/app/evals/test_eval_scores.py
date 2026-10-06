"""An eval writes its experiment to a ScoreStore (EVALS_PLAN D34, D35).

Gates: with ``scores=`` an eval leaves its experiment (running, then
finished), every item and every check's score in the store; those rows are
exactly what ``publish(record)`` makes of the job record; a store that
fails every write loses no verdict — the record is whole, the gate is the
same, the loss is counted — and ``publish`` afterwards fills the store;
without ``scores=`` nothing is written; a case run's own cost and tokens
are on its verdict and item.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from operonx.app.evals import Eval, Gate, budget, exact, publish, trajectory
from operonx.telemetry.scores import ScoreFilter, open_score_store
from operonx.telemetry.scores.sqlite import SqliteScoreStore
from tests.internal.app.evals._fake_llm import PRICE_IN, PRICE_OUT, USAGE
from tests.internal.app.evals._flows import flow

ANSWER = "tool_message.content"
CASES = [
    {
        "id": "order",
        "input": "lookup order 42",
        "expected": {"tool_message": {"content": "shipped"}},
        "tags": ["refund"],
    },
    {"id": "chat", "input": "hello", "expected": {"tool_message": {"content": "shipped"}}},
]
COST = USAGE["prompt_tokens"] * PRICE_IN + USAGE["completion_tokens"] * PRICE_OUT


def _eval(tmp_path: Path, scores=None, **kw) -> Eval:
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in CASES), encoding="utf-8")
    kw.setdefault("trace", [])
    return Eval(
        "stored",
        graph=flow,
        input="text",
        dataset=path,
        evaluators=[
            exact(ANSWER),
            trajectory.ops(["classify"], mode="superset"),
            budget(llm_calls=1),
        ],
        record_dir=tmp_path / "evals",
        scores=scores,
        **kw,
    )


@pytest.fixture
def logged():
    """What operonx's logger said (it does not propagate to pytest's caplog)."""
    seen = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    logger, handler = logging.getLogger("operonx.core"), Keep()
    logger.addHandler(handler)
    try:
        yield seen
    finally:
        logger.removeHandler(handler)


def _plain(rows):
    """Rows as comparable dicts (a score's created_at is the experiment's start)."""
    return sorted(
        (r.to_dict() for r in rows), key=lambda d: json.dumps(d, sort_keys=True, default=str)
    )


async def test_an_eval_writes_its_experiment_items_and_scores(tmp_path, llm):
    store = SqliteScoreStore(path=tmp_path / "scores.sqlite")
    run = await _eval(tmp_path, scores=store, repeats=2).run()

    rec = store.get_experiment(run.run_id)
    exp = rec.experiment
    assert (exp.eval, exp.status, exp.repeats, exp.cases) == ("stored", "failed", 2, 2)
    assert exp.gate == run.meta["eval"]["gate"] and exp.metrics == run.meta["eval"]["metrics"]
    fp = run.meta["eval"]["fingerprint"]
    assert (exp.dataset, exp.dataset_version, exp.graph_hash) == (
        "cases",
        fp["dataset_version"],
        fp["graph_hash"],
    )
    assert exp.ended_at >= exp.started_at > 0
    assert exp.cost_usd == pytest.approx(4 * COST)  # four case runs, one priced LLM call each

    assert sorted((i.case_id, i.repeat) for i in rec.items) == [
        ("chat", 0),
        ("chat", 1),
        ("order", 0),
        ("order", 1),
    ]
    order = next(i for i in rec.items if i.case_id == "order")
    assert (
        order.passed is True and order.tags == ["refund"] and order.cost_usd == pytest.approx(COST)
    )
    assert (order.tokens_in, order.tokens_out) == (12, 4)
    assert order.trace_id == next(i.trace_id for i in run.items if i.key == "order#0")

    scores = store.scores(ScoreFilter(experiment_id=run.run_id))
    assert len(scores) == 4 * 3
    by = {(s.case_id, s.repeat, s.score_name): s for s in scores}
    s = by[("chat", 0, f"exact({ANSWER})")]
    assert (s.passed, s.value, s.source, s.target, s.origin, s.name) == (
        False,
        0.0,
        "code",
        "item",
        "eval",
        "stored",
    )
    assert s.evaluator_version == fp["evaluators"][f"exact({ANSWER})"] and s.reason.startswith(
        "got None"
    )
    assert by[("order", 1, "trajectory.ops(superset)")].data_type == "numeric"  # it gives a score
    assert {s.created_at for s in scores} == {exp.started_at}


async def test_the_live_rows_are_what_publish_makes_of_the_record(tmp_path, llm):
    live = SqliteScoreStore(path=tmp_path / "live.sqlite")
    run = await _eval(tmp_path, scores=live).run()
    later = SqliteScoreStore(path=tmp_path / "later.sqlite")
    counts = publish(run, later)
    assert counts == {"experiments": 1, "items": 2, "scores": 6}

    a, b = live.get_experiment(run.run_id), later.get_experiment(run.run_id)
    assert a.experiment == b.experiment and a.items == b.items
    assert _plain(live.scores()) == _plain(later.scores())
    publish(run, later)  # again: the same rows, not more
    assert len(later.scores()) == 6 and len(later.get_experiment(run.run_id).items) == 2


class _Down(SqliteScoreStore):
    """A store whose every write fails, like a ClickHouse that is down."""

    def __init__(self, path):
        super().__init__(path=path)
        self.attempts = 0
        self.lock = threading.Lock()

    def _put(self, table, objs):
        with self.lock:
            self.attempts += 1
        raise ConnectionError("the score store is down")


async def test_a_store_outage_loses_no_verdict(tmp_path, llm, logged):
    down = _Down(tmp_path / "down.sqlite")
    t0 = time.perf_counter()
    run = await _eval(tmp_path, scores=down, scores_timeout=1.0, gate=Gate(threshold=0.4)).run()
    took = time.perf_counter() - t0
    assert down.attempts > 0  # it was tried…
    assert took < 6, took  # …and waited for no longer than scores_timeout plus the run

    # the record is whole and the gate decided as it would have
    reference = await _eval(tmp_path, gate=Gate(threshold=0.4)).run()
    assert [i.key for i in sorted(run.items, key=lambda i: i.key)] == ["chat", "order"]
    assert all(i.verdict and "checks" in i.verdict for i in run.items)
    assert (
        run.meta["eval"]["gate"]["verdict"] == reference.meta["eval"]["gate"]["verdict"] == "pass"
    )
    said = " ".join(logged)
    assert "score store" in said and "publish" in said and str(run.path) in said

    # when the store is back, the record fills it
    up = SqliteScoreStore(path=tmp_path / "down.sqlite")
    publish(run, up)
    assert up.get_experiment(run.run_id).experiment.status == "ok"
    assert len(up.scores(ScoreFilter(experiment_id=run.run_id))) == 6


async def test_without_scores_nothing_is_written_and_the_cost_is_on_the_verdict(
    tmp_path, llm, monkeypatch
):
    monkeypatch.setenv("OPERONX_RUNS_DIR", str(tmp_path / "runs"))
    run = await _eval(tmp_path).run()
    assert not (tmp_path / "runs" / "scores").exists()
    order = next(i.verdict for i in run.items if i.key == "order")
    assert order["cost_usd"] == pytest.approx(COST)
    assert (order["tokens_in"], order["tokens_out"]) == (12, 4)


async def test_scores_take_a_key_or_a_spec(tmp_path, llm, monkeypatch):
    monkeypatch.setenv("OPERONX_RUNS_DIR", str(tmp_path / "runs"))
    run = await _eval(tmp_path, scores={"backend": "files"}).run()
    files = open_score_store({"backend": "files"})
    assert files.get_experiment(run.run_id).experiment.status == "failed"
    with pytest.raises(TypeError, match="scores"):
        _eval(tmp_path, scores=42)
    with pytest.raises(ValueError, match="score_store:"):
        _eval(tmp_path, scores="runs:default")


async def test_a_rescore_writes_its_scores_with_rescore_ids(tmp_path, llm):
    from operonx.telemetry.runs.files import FilesRunStore

    runs = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    store = SqliteScoreStore(path=tmp_path / "scores.sqlite")
    ev = _eval(tmp_path, scores=store, trace=[runs])
    run = await ev.run()
    again = await ev.rescore(run.run_id, [budget(llm_calls=0)], store=runs, scores=store)
    assert again.summary["passed"] == 0
    new = store.scores(ScoreFilter(experiment_id=run.run_id, score_name="budget"))
    assert len(new) == 2 + 2  # the run's own budget(llm_calls=1) items, and the rescore's traces
    rescored = [s for s in new if s.target == "trace"]
    assert {s.passed for s in rescored} == {False} and {s.trace_id for s in rescored} == {
        i.trace_id for i in run.items
    }
    assert all(s.evaluator_version for s in rescored)


MOD = """
from operonx.core import END, START, graph, op

@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}

@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END

def label_ok(output=None, expected=None):
    return output["label"] == expected["label"]
"""


def _project(tmp_path, scores):
    import textwrap
    import uuid

    name = f"ev_{uuid.uuid4().hex[:6]}"
    app = (
        "\nfrom operonx.app import Application, Eval\n\n"
        'APP = Application("evdemo", jobs=[Eval("labels", graph=flow, input="text", '
        f'dataset="dataset:labels", evaluators=[label_ok], scores={scores!r})])\n'
    )
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(MOD) + app, encoding="utf-8")
    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets" / "labels.jsonl").write_text(
        '{"id": "a", "input": "money back", "expected": {"label": "refund"}}\n', encoding="utf-8"
    )
    (tmp_path / "resources.yaml").write_text(
        f"score_store:\n  team:\n    backend: sqlite\n    path: {tmp_path / 'team.sqlite'}\n",
        encoding="utf-8",
    )
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "evdemo"\napp = "{name}:APP"\n\n[resources]\n'
        'overlay = "resources.yaml"\n',
        encoding="utf-8",
    )
    return name


def test_a_declared_eval_writes_to_its_score_store(tmp_path, monkeypatch):
    import sys

    from operonx.app import Application
    from operonx.core.registry import ResourceHub

    name = _project(tmp_path, "score_store:team")
    monkeypatch.chdir(tmp_path)
    try:
        app = Application.find(tmp_path)
        run = app.run_sync("labels")
        team = SqliteScoreStore(path=tmp_path / "team.sqlite")
        assert team.get_experiment(run.run_id).experiment.status == "ok"
        assert [s.passed for s in team.scores()] == [True]
    finally:
        sys.modules.pop(name, None)
        ResourceHub.reset_instance()


def test_scores_must_be_a_score_store_key(tmp_path):
    from operonx.app import Eval

    (tmp_path / "d.jsonl").write_text('{"id": "a", "input": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="score_store:<name>"):
        Eval("labels", graph=flow, dataset=tmp_path / "d.jsonl", scores="team.sqlite")


async def test_a_store_that_cannot_be_opened_fails_before_the_record(tmp_path, llm):
    from operonx.core.registry import ResourceHub

    path = tmp_path / "resources.yaml"  # the stand-in model's, plus another store
    path.write_text(
        path.read_text(encoding="utf-8") + "score_store:\n  other:\n    backend: sqlite\n",
        encoding="utf-8",
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
    ev = _eval(tmp_path, scores="score_store:missing")
    with pytest.raises(Exception, match="score_store:missing"):
        await ev.run()
    assert not (tmp_path / "evals" / "stored").exists()  # no record left "running"
