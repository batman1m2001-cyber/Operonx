"""Review queues and reviews as scores (EVALS_PLAN §14 D72–D74)."""

from __future__ import annotations

import json

import pytest

from operonx.app.evals.align import align
from operonx.app.evals.queues import (
    MIN_SHARED,
    REVIEW,
    QueueSpec,
    enqueue,
    migrate_reviews,
    pending,
    queue_agreement,
    queue_items,
    review,
    reviews_as_scores,
)
from operonx.telemetry.scores import Score, ScoreFilter, open_score_store


@pytest.fixture
def store(tmp_path):
    return open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})


def test_a_queue_spec_is_checked():
    with pytest.raises(ValueError, match="names a file"):
        QueueSpec("a/b")
    with pytest.raises(ValueError, match="rubric types"):
        QueueSpec("q", rubric={"polite": "yes-no"})
    with pytest.raises(ValueError, match="leave it out"):
        QueueSpec("q", rubric={"review": "bool"})
    with pytest.raises(ValueError, match="reviewers"):
        QueueSpec("q", reviewers=0)


def test_the_same_thing_added_twice_is_one_item(tmp_path):
    a = enqueue(tmp_path, "q", trace_id="t1", source="online:x", reason="polite")
    b = enqueue(tmp_path, "q", trace_id="t1", source="runs", reason="again")
    enqueue(tmp_path, "q", target="item", experiment_id="e1", case_id="c1")

    assert a == b
    items = queue_items(tmp_path, "q")
    assert [i.target for i in items] == ["trace", "item"]
    assert items[0].reason == "again"  # the latest line wins, the first add keeps its place
    with pytest.raises(ValueError, match="needs trace_id"):
        enqueue(tmp_path, "q", target="op", op_id="x")


def test_an_item_is_done_when_enough_people_reviewed_it(tmp_path, store):
    spec = QueueSpec("q", reviewers=2)
    enqueue(tmp_path, "q", trace_id="t1")
    enqueue(tmp_path, "q", trace_id="t2")
    review(store, author="ann", verdict="good", trace_id="t1", queue="q")

    left = {i.trace_id: who for i, who in pending(store, tmp_path, spec)}
    assert left == {"t1": ["ann"], "t2": []}

    review(store, author="bob", verdict="bad", trace_id="t1", queue="q")
    review(store, author="ann", verdict="bad", trace_id="t1", queue="q")  # replaces her first
    assert [i.trace_id for i, _ in pending(store, tmp_path, spec)] == ["t2"]
    # a review outside the queue does not count toward it
    review(store, author="cy", verdict="good", trace_id="t2")
    assert [i.trace_id for i, _ in pending(store, tmp_path, spec)] == ["t2"]


def test_a_review_is_human_scores(store):
    rows = review(
        store,
        author="ann",
        verdict="bad",
        labels=["rude", " rude", ""],
        note="interrupted the caller",
        rubric={"polite": False, "resolved": "partly", "wait_s": 4},
        trace_id="t1",
        queue="q",
    )
    got = {s.score_name: s for s in store.scores(ScoreFilter(trace_id="t1"))}

    assert set(got) == {REVIEW, "polite", "resolved", "wait_s"} and len(rows) == 4
    r = got[REVIEW]
    assert (r.source, r.label, r.passed, r.author, r.queue) == ("human", "bad", False, "ann", "q")
    assert r.metadata["labels"] == ["rude"] and r.reason == "interrupted the caller"
    assert got["polite"].passed is False and got["resolved"].label == "partly"
    assert got["wait_s"].value == 4.0
    with pytest.raises(ValueError, match="good, bad"):
        review(store, author="ann", verdict="meh", trace_id="t1")


def test_agreement_is_measured_only_with_enough_shared_items(store):
    for i in range(MIN_SHARED - 1):
        review(store, author="ann", verdict="good", trace_id=f"t{i}", queue="q")
        review(store, author="bob", verdict="good", trace_id=f"t{i}", queue="q")
    few = queue_agreement(store, "q")
    assert few[REVIEW]["kappa"] is None and "fewer than" in few[REVIEW]["note"]

    # ann and bob agree on 16 of 20: 8 good/good, 8 bad/bad, 4 split
    for i in range(20):
        a = "good" if i < 10 else "bad"
        b = a if i not in (0, 1, 10, 11) else ("bad" if a == "good" else "good")
        review(store, author="ann", verdict=a, trace_id=f"s{i}", queue="r")
        review(store, author="bob", verdict=b, trace_id=f"s{i}", queue="r")
    got = queue_agreement(store, "r")[REVIEW]
    assert got["shared"] == 20 and got["agreement"] == pytest.approx(0.8)
    assert got["kappa"] == pytest.approx(0.6)


def test_studio_reviews_read_as_scores_and_migrate_once(tmp_path, store):
    path = tmp_path / "reviews.jsonl"
    lines = [
        {"run": "t1", "verdict": "good", "labels": [], "note": "", "user": "ann", "at": 10.0},
        {
            "run": "t1",
            "verdict": "bad",
            "labels": ["slow"],
            "note": "late",
            "user": "ann",
            "at": 11.0,
        },
        {"run": "t2", "verdict": None, "labels": ["odd"], "note": "", "user": "bob", "at": 12.0},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")

    rows = {s.trace_id: s for s in reviews_as_scores(path)}
    assert rows["t1"].label == "bad" and rows["t1"].metadata["labels"] == ["slow"]
    assert rows["t2"].label is None and rows["t2"].author == "bob"

    assert migrate_reviews(path, store) == 2
    assert migrate_reviews(path, store) == 2
    assert len(store.scores(ScoreFilter(score_name=REVIEW))) == 2


def test_a_judge_aligns_against_reviews(store):
    for i in range(12):
        verdict = "good" if i % 3 else "bad"
        review(store, author="ann", verdict=verdict, trace_id=f"t{i}")
        store.put_scores(
            [
                Score(
                    score_name=REVIEW,
                    target="trace",
                    source="judge",
                    passed=verdict == "good",
                    trace_id=f"t{i}",
                    evaluator_version="v1",
                )
            ]
        )
    result = align(store, REVIEW)
    assert result.n == 12 and result.kappa == pytest.approx(1.0)
