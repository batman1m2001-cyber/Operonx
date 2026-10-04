"""Judges are operonx graphs (EVALS_PLAN D49–D55, D57, D58, D61).

Gates: a judge in an eval is its own traced run — ``origin=eval``,
``role=judge``, ``judged_trace`` the case's run — and its verdict names
that trace; the case's run holds none of the judge's calls, so the
system's cost and the judges' cost are each their own calls' price; the
verdict is PASS/FAIL with the model's reason kept, and an answer outside
the labels is retried, then fails its check; the version moves with the
rubric, examples, labels, temperature and the model in ``resources.yaml``
— not with the API key; with a score store a second run makes no call at
all; ``judge_cache=False`` calls again; ``reference`` decides what the
model sees; a user's ``@graph`` evaluator is traced the same way and, like
every judge, is not rescored; ``judge_concurrency`` bounds the calls in
flight; the judge's model equal to the system's is warned about; the
1.9.0 ``llm_judge`` is the same machinery under its old contract.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from operonx.app.evals import Eval, judge, llm_judge, rescore
from operonx.app.evals.fingerprint import evaluator_version
from operonx.core import END, START, graph, op
from operonx.core.registry import ResourceHub
from operonx.telemetry.runs.files import FilesRunStore
from operonx.telemetry.scores import open_score_store
from tests.internal.app.evals._fake_llm import PRICE_IN, PRICE_OUT, USAGE, fake_llm, llm_hub
from tests.internal.app.evals._flows import flow

COST = USAGE["prompt_tokens"] * PRICE_IN + USAGE["completion_tokens"] * PRICE_OUT

# ── the stand-in judge ───────────────────────────────────────────────────


def _messages(body):
    msgs = body.get("messages") or []
    system = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
    user = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "user")
    return system, user


def judge_answers(rule):
    """A stand-in model that answers judge prompts with ``rule(system,
    user)`` as the verdict (anything else gets the default answer)."""

    def answer(body):
        system, user = _messages(body)
        if '"verdict"' not in system:
            return None
        return json.dumps({"reason": "read the output", "verdict": rule(system, user)})

    return answer


def _judge_requests(server):
    return [b for b in server.requests if '"verdict"' in _messages(b)[0]]


@pytest.fixture
def hub_reset():
    yield
    ResourceHub.reset_instance()


# ── systems under test ───────────────────────────────────────────────────


@op(bound="sync")
def respond(text: str = "") -> dict:
    return {"reply": "Your order 42 has shipped." if "order" in text else "Hello!"}


@graph
def bot(text: str = ""):
    r = respond(text=text)
    START >> r >> END


CASES = [
    {"id": "order", "input": "where is order 42", "expected": "says it shipped"},
    {"id": "hello", "input": "hi", "expected": "greets"},
]


def _dataset(tmp_path: Path, rows=CASES) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _eval(tmp_path, evaluators, *, graph_=bot, rows=CASES, name="judged", **kw) -> Eval:
    kw.setdefault("trace", [])
    return Eval(
        name,
        graph=graph_,
        item_input="text",
        dataset=_dataset(tmp_path, rows),
        evaluators=evaluators,
        record_dir=tmp_path / "evals",
        **kw,
    )


def on_topic(**kw):
    return judge("llm:judge", "The reply answers what the user asked.", name="on_topic", **kw)


def _shipped(system, user):
    return "PASS" if "shipped" in user else "FAIL"


# ── traced as its own run; cost kept apart ───────────────────────────────


def test_a_judge_is_its_own_traced_run_and_its_cost_is_not_the_system_s(tmp_path, hub_reset):
    runs = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    with fake_llm(judge_answers(lambda s, u: "PASS")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        ev = _eval(
            tmp_path,
            [on_topic()],
            graph_=flow,
            rows=[{"id": "order", "input": "lookup order 42"}, {"id": "chat", "input": "hello"}],
            trace=[runs],
        )
        run = ev.run_sync()

    s = run.meta["eval"]
    assert s["passed"] == 2, s
    for item in run.items:
        check = item.verdict["checks"]["judge:on_topic"]
        assert check["passed"] is True and check["label"] == "PASS"
        assert check["reason"] == "read the output"  # the rationale is kept

        judged = runs.get_run(check["judge_trace_id"])
        assert judged is not None and judged.meta["trace_id"] != item.trace_id
        meta = judged.meta["metadata"]
        assert meta["origin"] == "eval" and meta["role"] == "judge"
        assert meta["judged_trace"] == item.trace_id
        assert (meta["job"], meta["job_run"], meta["case"], meta["evaluator"]) == (
            "judged",
            run.run_id,
            item.key,
            "judge:on_topic",
        )
        assert judged.summary.llm_calls == 1

        # the case's own run: the system's one call, none of the judge's
        case_run = runs.get_run(item.trace_id)
        assert case_run.summary.llm_calls == 1
        assert item.verdict["cost_usd"] == pytest.approx(COST)
        assert check["cost_usd"] == pytest.approx(COST)

    assert s["cost_usd"] == pytest.approx(2 * COST)  # the system: two case runs, one call each
    assert s["judge_cost_usd"] == pytest.approx(2 * COST)  # the judge: one call per case
    j = s["judges"]["judge:on_topic"]
    assert (j["calls"], j["cached"], j["errors"]) == (2, 0, 0)
    assert j["cost_usd"] == pytest.approx(2 * COST)
    assert j["model"] == "judge-model" and j["version"] == evaluator_version(ev.evaluators[0])
    assert len(_judge_requests(server)) == 2


def test_the_prompt_is_binary_and_one_criterion(tmp_path, hub_reset):
    with fake_llm(judge_answers(_shipped)) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        run = _eval(tmp_path, [on_topic()]).run_sync()
    verdicts = {i.key: i.verdict["checks"]["judge:on_topic"] for i in run.items}
    assert verdicts["order"]["passed"] is True and verdicts["hello"]["passed"] is False
    assert verdicts["hello"]["label"] == "FAIL"

    # the two cases are judged at the same time: find the order case's request
    asked = [_messages(r) for r in _judge_requests(server)]
    system, user = next((m for m in asked if "order 42" in m[1]), asked[0])
    assert "The reply answers what the user asked." in system
    assert "PASS" in system and "FAIL" in system and '"reason"' in system
    assert "where is order 42" in user and "Your order 42 has shipped." in user


def test_an_answer_outside_the_labels_is_retried_then_fails_its_check(tmp_path, hub_reset):
    with fake_llm(judge_answers(lambda s, u: "MAYBE")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        run = _eval(tmp_path, [on_topic()], rows=CASES[:1]).run_sync()
    check = run.items[0].verdict["checks"]["judge:on_topic"]
    assert check["passed"] is False and "verdict" in check["error"], check
    assert len(_judge_requests(server)) == 2  # max_retries=1: asked twice
    j = run.meta["eval"]["judges"]["judge:on_topic"]
    assert j["errors"] == 1


def test_categorical_labels_pass_on_the_pass_labels(tmp_path, hub_reset):
    tone = judge(
        "llm:judge",
        "The tone of the reply.",
        name="tone",
        labels=("warm", "neutral", "cold"),
        pass_labels=("warm", "neutral"),
    )
    with fake_llm(judge_answers(lambda s, u: "cold" if "Hello" in u else "warm")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        run = _eval(tmp_path, [tone]).run_sync()
    got = {i.key: i.verdict["checks"]["judge:tone"] for i in run.items}
    assert (got["order"]["label"], got["order"]["passed"]) == ("warm", True)
    assert (got["hello"]["label"], got["hello"]["passed"]) == ("cold", False)


# ── version ──────────────────────────────────────────────────────────────


def test_the_version_is_rubric_examples_labels_temperature_and_model(tmp_path, hub_reset):
    def version(model="m-a", key="sk-local-test", **kw):
        llm_hub(tmp_path, "http://127.0.0.1:9/v1", more={"judge": model}, key=key)
        return evaluator_version(judge("llm:judge", kw.pop("rubric", "Polite?"), name="p", **kw))

    base = version()
    assert version() == base  # the same judge is the same version
    assert version(rubric="Kind?") != base
    assert version(examples=[{"input": "a", "output": "b", "verdict": "PASS"}]) != base
    assert version(labels=("YES", "NO"), pass_labels=("YES",)) != base
    assert version(temperature=0.5) != base
    assert version(model="m-b") != base  # a model swap in resources.yaml is a new judge
    assert version(key="sk-rotated") == base  # a rotated key is not


def test_a_rubric_file_names_the_judge(tmp_path, hub_reset):
    llm_hub(tmp_path, "http://127.0.0.1:9/v1", more={"judge": "m"})
    path = tmp_path / "polite.md"
    path.write_text("The reply is polite.", encoding="utf-8")
    j = judge("llm:judge", str(path))
    assert j.eval_name == "judge:polite" and j.rubric == "The reply is polite."
    with pytest.raises(ValueError, match="name="):
        judge("llm:judge", "The reply is polite.")  # inline text: say what it is called
    with pytest.raises(ValueError, match="no rubric file"):
        judge("llm:judge", str(tmp_path / "missing.md"))


# ── cache ────────────────────────────────────────────────────────────────


def test_a_cache_hit_makes_no_call(tmp_path, hub_reset):
    store = open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})
    with fake_llm(judge_answers(_shipped)) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        first = _eval(tmp_path, [on_topic()], scores=store).run_sync()
        assert len(_judge_requests(server)) == 2

        second = _eval(tmp_path, [on_topic()], scores=store).run_sync()
        assert len(_judge_requests(server)) == 2  # not one more call
        by_key = {i.key: i for i in second.items}
        for a in first.items:
            b = by_key[a.key]
            ca, cb = a.verdict["checks"]["judge:on_topic"], b.verdict["checks"]["judge:on_topic"]
            assert (cb["passed"], cb["label"], cb["reason"]) == (
                ca["passed"],
                ca["label"],
                ca["reason"],
            )
            assert cb["cached"] is True and "cached" not in ca
            assert cb["cost_usd"] == 0.0 and cb["tokens_in"] == 0  # spend, not value
            assert cb["judge_trace_id"] == ca["judge_trace_id"]  # the run that decided it
        j = second.meta["eval"]["judges"]["judge:on_topic"]
        assert (j["calls"], j["cached"]) == (0, 2)
        assert second.meta["eval"]["judge_cost_usd"] == 0.0

        _eval(tmp_path, [on_topic()], scores=store, judge_cache=False).run_sync()
        assert len(_judge_requests(server)) == 4  # asked again

        edited = judge("llm:judge", "The reply answers the question asked.", name="on_topic")
        _eval(tmp_path, [edited], scores=store).run_sync()
        assert len(_judge_requests(server)) == 6  # a new rubric is a new judge: a miss


def test_an_error_is_not_cached(tmp_path, hub_reset):
    store = open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})
    with fake_llm(judge_answers(lambda s, u: "MAYBE")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        _eval(tmp_path, [on_topic()], rows=CASES[:1], scores=store).run_sync()
        _eval(tmp_path, [on_topic()], rows=CASES[:1], scores=store).run_sync()
    assert len(_judge_requests(server)) == 4  # both runs asked (twice each)


# ── reference ────────────────────────────────────────────────────────────


def test_reference_decides_what_the_model_sees(tmp_path, hub_reset):
    rows = [CASES[0], {"id": "bare", "input": "hi"}]
    with fake_llm(judge_answers(lambda s, u: "PASS")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        _eval(tmp_path, [on_topic()], rows=rows).run_sync()  # auto
        shown = [_messages(b)[1] for b in _judge_requests(server)]
        assert sum("says it shipped" in u for u in shown) == 1  # only the case that has one
        server.requests.clear()

        _eval(tmp_path, [on_topic(reference=False)], rows=rows).run_sync()
        assert not any("says it shipped" in _messages(b)[1] for b in _judge_requests(server))
        server.requests.clear()

        run = _eval(tmp_path, [on_topic(reference=True)], rows=rows).run_sync()
        bare = next(i for i in run.items if i.key == "bare").verdict["checks"]["judge:on_topic"]
        assert bare["passed"] is False and "expected" in bare["error"]
        assert len(_judge_requests(server)) == 1  # the bare case was never sent


def test_include_trace_shows_the_run(tmp_path, hub_reset):
    with fake_llm(judge_answers(lambda s, u: "PASS")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        _eval(tmp_path, [on_topic(include_trace=True)], rows=CASES[:1]).run_sync()
    user = _messages(_judge_requests(server)[0])[1]
    assert "r (code) ok" in user and "Trace" in user  # ops by the names the graph gives them


# ── a user's graph evaluator ─────────────────────────────────────────────


@op(bound="sync")
def mentions_order(output: dict = None) -> dict:
    said = (output or {}).get("reply", "")
    return {"passed": "order" in said, "reason": f"said {said!r}"}


@graph
def order_check(output):
    m = mentions_order(output=output)
    START >> m >> END


def test_a_graph_evaluator_is_a_traced_judge(tmp_path):
    runs = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    run = _eval(tmp_path, [order_check], trace=[runs]).run_sync()
    got = {i.key: i.verdict["checks"]["order_check"] for i in run.items}
    assert got["order"]["passed"] is True and got["hello"]["passed"] is False
    meta = runs.get_run(got["order"]["judge_trace_id"]).meta["metadata"]
    order = next(i for i in run.items if i.key == "order")
    assert meta["role"] == "judge" and meta["judged_trace"] == order.trace_id
    assert run.meta["eval"]["judges"]["order_check"]["calls"] == 2


@op(bound="sync")
def checker_down(output: dict = None) -> dict:
    raise ConnectionError("judge backend unreachable")


@graph
def broken_check(output):
    m = checker_down(output=output)
    START >> m >> END


def test_a_judge_whose_graph_fails_names_the_op_and_its_error(tmp_path):
    """The failure is read from the run's ``$errors`` record — ``{type,
    message, count, first_ctx}`` — as the job runner reads an item's: the
    op's name and the last line of its message, not the record's repr."""
    run = _eval(tmp_path, [broken_check], rows=CASES[:1]).run_sync()
    check = run.items[0].verdict["checks"]["broken_check"]
    assert check["passed"] is False
    assert check["error"] == "m: ConnectionError: judge backend unreachable", check
    assert run.meta["eval"]["judges"]["broken_check"]["errors"] == 1


@graph
def wants_secret(secret):
    m = mentions_order(output=secret)
    START >> m >> END


def test_a_graph_evaluator_takes_only_what_a_case_provides(tmp_path):
    with pytest.raises(ValueError, match="secret"):
        _eval(tmp_path, [wants_secret])


async def test_judges_are_not_rescored(tmp_path):
    run = await _eval(tmp_path, [order_check]).run()
    with pytest.raises(ValueError, match="judge"):
        await rescore(run, [order_check])


# ── concurrency ──────────────────────────────────────────────────────────


def test_judge_concurrency_bounds_the_calls_in_flight(tmp_path, hub_reset):
    lock, now, most = threading.Lock(), [0], [0]

    def answer(body):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        time.sleep(0.15)
        with lock:
            now[0] -= 1
        return json.dumps({"reason": "ok", "verdict": "PASS"})

    rows = [{"id": f"c{i}", "input": f"order {i}"} for i in range(6)]
    with fake_llm(answer) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        run = _eval(
            tmp_path, [on_topic()], rows=rows, concurrency=6, judge_concurrency=2
        ).run_sync()
    assert run.meta["eval"]["passed"] == 6
    assert most[0] == 2


# ── self-preference ──────────────────────────────────────────────────────


def test_a_judge_of_the_system_s_own_model_is_warned_about(tmp_path, hub_reset):
    rows = [{"id": "order", "input": "lookup order 42"}]
    with fake_llm(judge_answers(lambda s, u: "PASS")) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "stand-in"})  # flow's own model
        same = _eval(tmp_path, [on_topic()], graph_=flow, rows=rows).run_sync()
        llm_hub(tmp_path, server.base_url, more={"judge": "other-model"})
        other = _eval(tmp_path, [on_topic()], graph_=flow, rows=rows).run_sync()

    s = same.meta["eval"]
    assert s["fingerprint"]["models"] == ["stand-in"]
    assert s["judges"]["judge:on_topic"]["self_preference"] is True
    assert any("self-preference" in w for w in s["gate"]["warnings"])
    o = other.meta["eval"]
    assert o["judges"]["judge:on_topic"]["self_preference"] is False
    assert not any("self-preference" in w for w in o["gate"].get("warnings", []))


# ── the 1.9.0 judge ──────────────────────────────────────────────────────


def test_llm_judge_is_a_traced_versioned_judge(tmp_path, hub_reset):
    def answer(body):
        system, user = _messages(body)
        if "Answer with JSON only" not in system:
            return None
        ok = "shipped" in user
        return json.dumps({"passed": ok, "score": 1.0 if ok else 0.0, "reason": "checked"})

    runs = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    with fake_llm(answer) as server:
        llm_hub(tmp_path, server.base_url, more={"judge": "judge-model"})
        old = llm_judge("llm:judge", "Did it answer {exactly}?")
        run = _eval(tmp_path, [old], trace=[runs]).run_sync()
    got = {i.key: i.verdict["checks"]["llm_judge"] for i in run.items}
    assert got["order"]["passed"] is True and got["order"]["score"] == 1.0
    assert got["hello"]["passed"] is False and got["hello"]["reason"] == "checked"
    assert runs.get_run(got["order"]["judge_trace_id"]).meta["metadata"]["role"] == "judge"
    assert old.eval_kind == "judge"
    system = _messages(server.requests[0])[0]
    assert "{exactly}" in system  # never formatted: braces are text
    assert re.search(r"judge-model", json.dumps(run.meta["eval"]["judges"]["llm_judge"]))
