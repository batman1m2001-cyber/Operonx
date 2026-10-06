"""Pairwise judging with a position swap (EVALS_PLAN D56, D57).

Gates: both orders are asked, as parallel branches of one judge run; a
judge that prefers the same answer in both orders names it; one whose
preference follows the position (always "A") makes every case a ``tie``
marked ``inconsistent``, and its swap-inconsistency rate is 1; the two
orders take as long as one; the preferences are ``pair`` scores; a judge
of the systems' own model is warned about; a pairwise judge is refused
as a case evaluator.
"""

from __future__ import annotations

import json
import time

import pytest

from operonx.app.evals import Eval, compare_pairwise, load_experiment, pairwise
from operonx.core import END, START, graph, op
from operonx.core.registry import ResourceHub
from operonx.telemetry.runs.files import FilesRunStore
from operonx.telemetry.scores import ScoreFilter, open_score_store
from tests.internal.app.evals._fake_llm import fake_llm, llm_hub
from tests.internal.app.evals._flows import flow


@pytest.fixture
def hub_reset():
    yield
    ResourceHub.reset_instance()


def _messages(body):
    msgs = body.get("messages") or []
    system = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
    user = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "user")
    return system, user


def _first_answer(user: str) -> str:
    return user.split("[Answer A]", 1)[1].split("[Answer B]", 1)[0]


def prefers_shipped(body):
    """A fair judge: the answer that says the order shipped, wherever it is."""
    system, user = _messages(body)
    if '"verdict"' not in system:
        return None
    first_has = "shipped" in _first_answer(user)
    second_has = "shipped" in user.split("[Answer B]", 1)[1]
    verdict = "TIE" if first_has == second_has else ("A" if first_has else "B")
    return json.dumps({"reason": "compared", "verdict": verdict})


def always_first(body):
    """A position-biased judge: always the first answer it is shown."""
    system, _ = _messages(body)
    if '"verdict"' not in system:
        return None
    return json.dumps({"reason": "the first one", "verdict": "A"})


@op(bound="sync")
def terse(text: str = "") -> dict:
    return {"reply": "ok"}


@op(bound="sync")
def helpful(text: str = "") -> dict:
    return {"reply": "Your order has shipped." if "order" in text else "Hello! How can I help?"}


@graph
def bot_a(text: str = ""):
    r = terse(text=text)
    START >> r >> END


@graph
def bot_b(text: str = ""):
    r = helpful(text=text)
    START >> r >> END


CASES = [
    {"id": "o1", "input": "where is order 1"},
    {"id": "o2", "input": "where is order 2"},
    {"id": "hi", "input": "hi"},
]


async def _experiments(tmp_path, graph_a=bot_a, graph_b=bot_b, rows=CASES):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = []
    for name, g in (("a", graph_a), ("b", graph_b)):
        run = await Eval(
            "pairs",
            graph=g,
            input="text",
            dataset=path,
            record_dir=tmp_path / "evals",
            trace=[],
            variant=name,
        ).run()
        out.append(load_experiment(run))
    return out


def helpfulness():
    return pairwise("llm:judge", "Which reply helps the user more?", name="helpful")


async def test_a_fair_judge_names_the_better_answer_in_both_orders(tmp_path, hub_reset):
    a, b = await _experiments(tmp_path)
    runs = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    store = open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})
    with fake_llm(prefers_shipped) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        got = await compare_pairwise(a, b, [helpfulness()], trace=[runs], scores=store)

    j = got["judges"]["pairwise:helpful"]
    cases = {c["case"]: c for c in j["cases"]}
    assert cases["o1"]["winner"] == "b" and cases["o2"]["winner"] == "b"
    assert cases["hi"]["winner"] == "tie" and cases["hi"]["inconsistent"] is False
    assert (j["wins_a"], j["wins_b"], j["ties"], j["inconsistent"], j["n"]) == (0, 2, 1, 0, 3)
    assert j["inconsistency_rate"] == 0.0
    assert j["preference"]["mean"] == pytest.approx((1 + 1 + 0.5) / 3)
    assert len(server.requests) == 6  # two orders per case

    # one judge run per case holds both orders
    meta = runs.get_run(cases["o1"]["judge_trace_id"])
    assert meta.meta["metadata"]["role"] == "judge" and meta.summary.llm_calls == 2

    scores = store.scores(ScoreFilter(target="pair"))
    assert {(s.case_id, s.label) for s in scores} == {("o1", "b"), ("o2", "b"), ("hi", "tie")}
    assert all(
        (s.experiment_id, s.pair_experiment_id, s.source, s.score_name)
        == (a.experiment_id, b.experiment_id, "judge", "pairwise:helpful")
        for s in scores
    )


async def test_a_position_biased_judge_is_a_tie_and_inconsistent(tmp_path, hub_reset):
    a, b = await _experiments(tmp_path)
    with fake_llm(always_first) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        got = await compare_pairwise(a, b, [helpfulness()])
    j = got["judges"]["pairwise:helpful"]
    assert all(c["winner"] == "tie" and c["inconsistent"] for c in j["cases"])
    assert (c := j["cases"][0])["ab"] == "a" and c["ba"] == "b"  # each order picked its first
    assert j["inconsistency_rate"] == 1.0 and j["ties"] == 3
    assert any("position" in w for w in j["warnings"])


async def test_both_orders_run_at_once(tmp_path, hub_reset):
    a, b = await _experiments(tmp_path, rows=CASES[:1])
    with fake_llm(prefers_shipped, delay=0.3) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        t0 = time.perf_counter()
        await compare_pairwise(a, b, [helpfulness()])
        took = time.perf_counter() - t0
    assert len(server.requests) == 2
    assert took < 0.55, took  # two 0.3 s answers side by side, not one after the other


async def test_a_judge_of_the_systems_own_model_is_warned_about(tmp_path, hub_reset):
    rows = [{"id": "o", "input": "lookup order 42"}]
    with fake_llm(prefers_shipped) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "stand-in"})
        a, b = await _experiments(tmp_path, flow, flow, rows=rows)
        same = await compare_pairwise(a, b, [helpfulness()])
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        other = await compare_pairwise(a, b, [helpfulness()])
    assert any("self-preference" in w for w in same["judges"]["pairwise:helpful"]["warnings"])
    assert not any("self-preference" in w for w in other["judges"]["pairwise:helpful"]["warnings"])


def test_a_pairwise_judge_is_not_a_case_evaluator(tmp_path):
    with pytest.raises(TypeError, match="compare_pairwise"):
        Eval(
            "x",
            graph=bot_a,
            dataset=tmp_path / "none.jsonl",
            evaluators=[pairwise("llm:judge", "Better?", name="better")],
        )
