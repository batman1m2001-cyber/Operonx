"""Judge alignment against human labels (EVALS_PLAN D59, D60).

Gates: Cohen's κ, TPR, TNR, accuracy and κ's standard error equal a
2×2 table worked by hand and a second, general computation; κ with one
class is undefined, not a number; judge and human scores meet on the same
target (trace, op, item), reviewers of one target vote and a tie is left
out, labels that say neither PASS nor FAIL are counted, not guessed; the
alignment record round-trips through the stores and is replaced, not
duplicated; an eval's gating judge with no record for its version, or κ
under 0.6, is reported loudly; an aligned one, or one that cannot move
the verdict, is not.
"""

from __future__ import annotations

import json
import math
from collections import Counter

import pytest

from operonx.app.evals import Eval, Gate, contains, load_experiment
from operonx.app.evals.align import (
    Alignment,
    align,
    alignment_of,
    cohen_kappa,
    record_alignment,
)
from operonx.app.evals.fingerprint import evaluator_version
from operonx.app.evals.report import markdown
from operonx.app.evals.stats import wilson
from operonx.core import END, START, graph, op
from operonx.telemetry.scores import Score, ScoreFilter, open_score_store

JUDGE = "judge:polite"


def _store(backend, tmp_path):
    if backend == "files":
        return open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    return open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})


def _judge_score(i, passed, *, version="v1", created_at=1000.0, experiment="e1", **kw):
    return Score(
        score_name=kw.pop("score_name", JUDGE),
        source="judge",
        target="item",
        experiment_id=experiment,
        case_id=f"c{i}",
        repeat=0,
        trace_id=f"t{i}",
        passed=passed,
        label="PASS" if passed else "FAIL",
        reason=f"reason {i}",
        evaluator_version=version,
        created_at=created_at,
        **kw,
    )


def _human_on_trace(i, passed, author="ann", **kw):
    return Score(
        score_name=kw.pop("score_name", JUDGE),
        source="human",
        target="trace",
        trace_id=f"t{i}",
        passed=passed,
        author=author,
        **kw,
    )


def _human_on_item(i, label, author="bob"):
    return Score(
        score_name=JUDGE,
        source="human",
        target="item",
        data_type="categorical",
        experiment_id="e1",
        case_id=f"c{i}",
        repeat=0,
        label=label,
        author=author,
    )


def _table(tp, fp, fn, tn):
    """(judge, human) pairs: tp judge PASS / human PASS, fp judge PASS /
    human FAIL, fn judge FAIL / human PASS, tn both FAIL."""
    return [(True, True)] * tp + [(True, False)] * fp + [(False, True)] * fn + [(False, False)] * tn


def _fill(store, pairs):
    judged, humans = [], []
    for i, (j, h) in enumerate(pairs):
        judged.append(_judge_score(i, j))
        # half the labels on the run, half on the item as good/bad: both are "the same target"
        humans.append(_human_on_trace(i, h) if i % 2 else _human_on_item(i, "good" if h else "bad"))
    store.put_scores(judged + humans)


# ── the statistics, by hand ──────────────────────────────────────────────


def _kappa_general(pairs):
    """κ over any labels: observed agreement against the agreement the two
    raters' own label rates give by chance — a second computation."""
    n = len(pairs)
    po = sum(1 for j, h in pairs if j == h) / n
    cj, ch = Counter(j for j, _ in pairs), Counter(h for _, h in pairs)
    pe = sum(cj[k] * ch[k] for k in set(cj) | set(ch)) / n**2
    return (po - pe) / (1 - pe)


def test_kappa_tpr_tnr_against_a_table_worked_by_hand():
    # tp 20, fp 5, fn 10, tn 15: n 50, p_o = 35/50 = 0.7,
    # p_e = (25·30 + 25·20) / 50² = 0.5, κ = (0.7 − 0.5) / (1 − 0.5) = 0.4
    k = cohen_kappa(20, 5, 10, 15)
    assert k["kappa"] == pytest.approx(0.4)
    assert k["kappa"] == pytest.approx(_kappa_general(_table(20, 5, 10, 15)))
    assert k["tpr"] == pytest.approx(20 / 30) and k["tnr"] == pytest.approx(15 / 20)
    assert k["accuracy"] == pytest.approx(0.7) and k["n"] == 50
    # Cohen (1960): SE = √(p_o(1 − p_o) / (n(1 − p_e)²)) = √(0.21 / 12.5)
    assert k["kappa_se"] == pytest.approx(math.sqrt(0.21 / 12.5))
    assert k["kappa_ci"][0] == pytest.approx(0.4 - 1.959964 * math.sqrt(0.0168), abs=1e-6)
    assert tuple(k["tpr_ci"]) == pytest.approx(wilson(20, 30))
    assert tuple(k["tnr_ci"]) == pytest.approx(wilson(15, 20))


@pytest.mark.parametrize("cells", [(3, 1, 2, 4), (10, 0, 0, 10), (7, 3, 3, 7), (1, 9, 9, 1)])
def test_kappa_matches_the_general_formula(cells):
    assert cohen_kappa(*cells)["kappa"] == pytest.approx(_kappa_general(_table(*cells)))


def test_kappa_with_one_class_is_undefined():
    k = cohen_kappa(12, 0, 0, 0)  # everyone said PASS every time
    assert k["kappa"] is None and k["tnr"] is None and k["accuracy"] == 1.0
    assert "undefined" in k["note"]


# ── the join ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_align_joins_judge_and_human_scores_on_the_same_target(backend, tmp_path):
    store = _store(backend, tmp_path)
    pairs = _table(20, 5, 10, 15)
    _fill(store, pairs)
    got = align(store, JUDGE)
    assert (got.tp, got.fp, got.fn, got.tn) == (20, 5, 10, 15)
    assert got.kappa == pytest.approx(0.4) and got.version == "v1"
    assert got.n == 50 and got.unmatched_judge == 0 and got.unmatched_human == 0
    # the disagreements, with the judge's reason
    assert len(got.disagreements) == 15
    assert {d["judge"] for d in got.disagreements} == {"PASS", "FAIL"}
    assert all(d["reason"].startswith("reason ") for d in got.disagreements)


def test_reviewers_vote_and_unclear_labels_are_counted(tmp_path):
    store = _store("sqlite", tmp_path)
    store.put_scores(
        [
            _judge_score(0, True),
            _judge_score(1, True),
            _judge_score(2, False),
            _judge_score(3, True),  # nobody reviewed it
            # t0: two of three say PASS → PASS
            _human_on_trace(0, True, "ann"),
            _human_on_trace(0, True, "bob"),
            _human_on_trace(0, False, "cy"),
            # t1: one each → a tie, left out
            _human_on_trace(1, True, "ann"),
            _human_on_trace(1, False, "bob"),
            # c2: a label that says neither
            _human_on_item(2, "maybe"),
            # a human score on a run no judge saw
            _human_on_trace(9, True, "ann"),
        ]
    )
    got = align(store, JUDGE)
    assert (got.tp, got.fp, got.fn, got.tn) == (1, 0, 0, 0)
    assert got.human_ties == 1 and got.unusable_human == 1
    assert got.unmatched_judge == 2  # t2 (its label was unusable) and t3
    assert got.unmatched_human == 1


def test_op_scores_meet_only_op_scores(tmp_path):
    store = _store("sqlite", tmp_path)
    store.put_scores(
        [
            Score(
                score_name=JUDGE,
                source="judge",
                target="op",
                trace_id="t1",
                op_id="o1",
                passed=False,
                evaluator_version="v1",
            ),
            Score(
                score_name=JUDGE,
                source="human",
                target="op",
                trace_id="t1",
                op_id="o1",
                passed=False,
                author="ann",
            ),
            _human_on_trace(1, True),  # the run as a whole: not the op
        ]
    )
    got = align(store, JUDGE)
    assert (got.tp, got.fp, got.fn, got.tn) == (0, 0, 0, 1)


def test_the_newest_version_is_aligned_unless_one_is_named(tmp_path):
    store = _store("sqlite", tmp_path)
    store.put_scores(
        [
            _judge_score(0, False, version="old", created_at=100.0, experiment="e0"),
            _judge_score(0, True, version="new", created_at=200.0),
            _human_on_trace(0, True),
        ]
    )
    assert align(store, JUDGE).version == "new" and align(store, JUDGE).tp == 1
    old = align(store, JUDGE, version="old")
    assert (old.version, old.fn) == ("old", 1)
    assert align(store, JUDGE, experiment="e0").version == "old"


def test_humans_under_another_name(tmp_path):
    store = _store("sqlite", tmp_path)
    store.put_scores([_judge_score(0, True), _human_on_trace(0, True, score_name="review")])
    assert align(store, JUDGE).n == 0
    assert align(store, JUDGE, human="review").tp == 1


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_the_record_round_trips_and_is_replaced(backend, tmp_path):
    store = _store(backend, tmp_path)
    _fill(store, _table(20, 5, 10, 15))
    first = record_alignment(store, align(store, JUDGE))
    assert first.target == "evaluator" and first.value == pytest.approx(0.4)
    assert first.passed is False  # κ < 0.6
    got = alignment_of(store, JUDGE, "v1")
    assert got is not None and got["kappa"] == pytest.approx(0.4) and got["n"] == 50
    assert got["confusion"] == {"tp": 20, "fp": 5, "fn": 10, "tn": 15}

    store.put_scores([_judge_score(60, True), _human_on_trace(60, True)])
    record_alignment(store, align(store, JUDGE))
    rows = store.scores(ScoreFilter(score_name=JUDGE, target="evaluator"))
    assert len(rows) == 1 and rows[0].metadata["n"] == 51  # replaced, not a second row
    assert alignment_of(store, JUDGE, "v2") is None


# ── the warning in an eval's report ──────────────────────────────────────


@op(bound="sync")
def polite_check(output: dict = None) -> dict:
    return {"passed": "please" in str(output), "reason": "looked for please"}


@graph
def polite(output):
    p = polite_check(output=output)
    START >> p >> END


@op(bound="sync")
def answer(text: str = "") -> dict:
    return {"reply": f"please wait about {text}"}


@graph
def bot(text: str = ""):
    a = answer(text=text)
    START >> a >> END


def _eval(tmp_path, store, gate=None):
    path = tmp_path / "cases.jsonl"
    rows = [{"id": "a", "input": "x"}, {"id": "b", "input": "y"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return Eval(
        "aligned",
        graph=bot,
        input="text",
        dataset=path,
        evaluators=[polite, contains("please")],
        gate=gate,
        scores=store,
        record_dir=tmp_path / "evals",
        trace=[],
    )


def _record(store, version, cells):
    """An alignment record for *version*, as `align` would make one."""
    record_alignment(store, Alignment.of_counts("polite", version, "polite", *cells))


def _warnings(run):
    return [w for w in run.meta["eval"]["gate"].get("warnings", []) if "UNVALIDATED JUDGE" in w]


def test_an_unvalidated_gating_judge_is_reported_loudly(tmp_path):
    store = _store("sqlite", tmp_path)
    run = _eval(tmp_path, store).run_sync()
    (w,) = _warnings(run)
    assert "polite" in w and "no alignment record" in w and "operonx eval align" in w
    assert run.meta["eval"]["judges"]["polite"]["alignment"] is None
    assert run.meta["eval"]["gate"]["verdict"] == "pass"  # warned, not failed
    md = markdown(load_experiment(run))
    assert md.index("UNVALIDATED JUDGE") < md.index("### Metrics")


def test_a_low_kappa_is_reported_and_a_high_one_is_not(tmp_path):
    store = _store("sqlite", tmp_path)
    version = evaluator_version(_eval(tmp_path, store).evaluators[0])

    _record(store, version, (20, 5, 10, 15))  # κ 0.4
    (w,) = _warnings(_eval(tmp_path, store).run_sync())
    assert "κ = 0.40" in w and "< 0.6" in w

    _record(store, version, (45, 2, 3, 50))  # κ ≈ 0.90
    run = _eval(tmp_path, store).run_sync()
    assert _warnings(run) == []
    got = run.meta["eval"]["judges"]["polite"]["alignment"]
    assert got["kappa"] == pytest.approx(cohen_kappa(45, 2, 3, 50)["kappa"])
    assert "polite" in markdown(load_experiment(run)) and "0.90" in markdown(load_experiment(run))


def test_a_record_for_another_version_does_not_validate_this_one(tmp_path):
    store = _store("sqlite", tmp_path)
    _record(store, "an-older-rubric", (45, 2, 3, 50))
    (w,) = _warnings(_eval(tmp_path, store).run_sync())
    assert "no alignment record" in w and "an-older-rubric" in w


def test_a_judge_that_cannot_move_the_verdict_is_not_warned_about(tmp_path):
    store = _store("sqlite", tmp_path)
    gate = Gate(threshold={"contains": 0.5}, must_pass_tag=None)  # only `contains` decides
    run = _eval(tmp_path, store, gate=gate).run_sync()
    assert _warnings(run) == []
    assert run.meta["eval"]["judges"]["polite"]["gating"] is False


def test_without_a_score_store_there_is_no_record_to_read(tmp_path):
    run = _eval(tmp_path, None).run_sync()
    (w,) = _warnings(run)
    assert "no score store" in w


def test_a_judge_that_errored_is_not_counted_as_fail(tmp_path):
    """Found measuring real data: a judge answer that did not parse is a
    failed check (passed False), but it is no FAIL verdict — counting it
    would charge the judge with a disagreement it never made."""
    from operonx.app.evals.publish import check_score

    store = _store("sqlite", tmp_path)
    broken = check_score(
        JUDGE,
        {"passed": False, "error": "the judge's answer did not parse: mismatched tag"},
        source="judge",
        evaluator_version="v1",
        target="trace",
        trace_id="t0",
    )
    assert broken.metadata["error"].startswith("the judge's answer did not parse")
    store.put_scores(
        [broken, _judge_score(1, False), _human_on_trace(0, True), _human_on_trace(1, False)]
    )
    got = align(store, JUDGE)
    assert (got.tp, got.fp, got.fn, got.tn) == (0, 0, 0, 1)
    assert got.judge_errors == 1
