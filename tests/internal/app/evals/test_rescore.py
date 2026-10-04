"""`rescore`: judge a recorded eval run again, without running its graph
(EVALS_PLAN D29).

Gates: the eval's own deterministic evaluators reproduce its verdicts
exactly (their ``ms`` aside); no op runs and no trace is written; trace
evaluators read the stored runs; a case edited since the run, and an
output the record clipped, are errors on their items, not silent passes;
a judge is refused; an evaluator that needs the trace without a store is
refused at once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operonx.app.evals import Eval, budget, contains, exact, llm_judge, rescore, trajectory
from operonx.core import END, START, graph, op
from operonx.telemetry.runs.files import FilesRunStore
from tests.internal.app.evals._flows import CALLS, flow

ANSWER = "tool_message.content"
CASES = [
    {
        "id": "order",
        "input": "lookup order 42",
        "expected": {"tool_message": {"content": "shipped"}},
        "trajectory": {"ops": ["classify", "lookup_order", "reply", "run_tool"]},
    },
    {
        "id": "chat",
        "input": "hello",
        "expected": {"tool_message": {"content": "shipped"}},  # fails: nothing to ship
        "trajectory": {"ops": ["classify", "lookup_order", "reply", "run_tool"]},
    },
]


def _write(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _evaluators():
    return [
        exact(ANSWER),
        trajectory.ops(mode="strict"),
        budget(llm_calls=1, tokens=16),
        contains("shipped"),
    ]


@pytest.fixture
def ran(tmp_path, llm):
    """An eval traced into a files store, run twice (repeats=2), recorded."""
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    ev = Eval(
        "rescored",
        graph=flow,
        item_input="text",
        dataset=_write(tmp_path / "cases.jsonl", CASES),
        evaluators=_evaluators(),
        repeats=2,
        record_dir=tmp_path / "evals",
        trace=[store],
    )
    run = ev.run_sync()
    return ev, run, store


def _without_ms(verdict):
    out = dict(verdict)
    out["checks"] = {
        k: {kk: vv for kk, vv in c.items() if kk != "ms"} for k, c in verdict["checks"].items()
    }
    return out


async def test_the_eval_s_own_checks_reproduce_its_verdicts_without_a_run(ran):
    ev, run, store = ran
    before, runs_before = dict(CALLS), store.count()
    again = await rescore(run, _evaluators(), store=store)
    assert dict(CALLS) == before  # no op body ran
    assert store.count() == runs_before == 4  # and no trace was written

    assert set(again.verdicts) == {i.key for i in run.items}
    for item in run.items:
        assert _without_ms(again.verdicts[item.key]) == _without_ms(item.verdict)
    ev_meta = run.meta["eval"]
    assert again.summary["pass_rate"] == ev_meta["pass_rate"] == 0.5
    assert again.summary["checks"] == ev_meta["checks"]
    assert again.summary["metrics"] == ev_meta["metrics"]
    assert (again.run_id, again.eval) == (run.run_id, "rescored")


async def test_new_evaluators_read_the_stored_runs(ran):
    ev, run, store = ran
    again = await rescore(run.path, [budget(llm_calls=0)], store=store)  # a run directory works too
    assert again.summary["passed"] == 0
    v = again.verdicts["order#0"]["checks"]["budget"]
    assert v["passed"] is False and v["measured"] == {"llm_calls": 1}


async def test_eval_rescore_uses_the_eval_s_evaluators_and_skips_its_judges(ran):
    ev, run, store = ran
    judged = Eval(
        "rescored",
        graph=flow,
        item_input="text",
        dataset=ev.dataset,
        evaluators=[*_evaluators(), llm_judge("llm:bot", "Is it polite?")],
        record_dir=ev.record_dir,
    )
    again = await judged.rescore(run.run_id, store=store)
    assert again.skipped == ["llm_judge"]
    assert {_without_ms(v)["checks"].keys().__len__() for v in again.verdicts.values()} == {4}
    assert again.summary["pass_rate"] == 0.5


async def test_a_case_edited_since_the_run_errors_on_its_items(ran, tmp_path):
    ev, run, store = ran
    rows = [dict(CASES[0]), dict(CASES[1], expected={"tool_message": {"content": None}})]
    again = await rescore(
        run, _evaluators(), store=store, dataset=_write(tmp_path / "edited.jsonl", rows)
    )
    assert again.verdicts["order#1"]["passed"] is True
    for key in ("chat#0", "chat#1"):
        v = again.verdicts[key]
        assert v["passed"] is False and "changed since the run" in v["error"]
    assert again.summary["errored"] == 2


async def test_judges_are_refused_and_a_trace_check_needs_a_store(ran):
    ev, run, store = ran
    with pytest.raises(ValueError, match="deterministic"):
        await rescore(run, [llm_judge("llm:bot", "Is it polite?")], store=store)
    with pytest.raises(ValueError, match="store="):
        await rescore(run, [budget(llm_calls=1)])
    from operonx.app.jobs import Job

    plain = await Job(
        "plain", graph=flow, source=[{"text": "hi"}], item_input="text", record_dir=ev.record_dir
    ).run()
    with pytest.raises(ValueError, match="not an eval run"):
        await rescore(plain, [exact()])


@op
def long_answer(text: str = "") -> dict:
    return {"text": text * 5000}


@graph
def verbose(text: str = ""):
    a = long_answer(text=text)
    START >> a >> END


async def test_an_output_the_record_clipped_cannot_be_rescored(tmp_path):
    ev = Eval(
        "clipped",
        graph=verbose,
        item_input="text",
        dataset=_write(
            tmp_path / "c.jsonl", [{"id": "big", "input": "ab"}, {"id": "small", "input": ""}]
        ),
        evaluators=[contains("ab")],
        record_dir=tmp_path / "evals",
        trace=[],
    )
    run = await ev.run()
    big = next(i for i in run.items if i.key == "big")
    assert big.verdict["output_clipped"] is True and isinstance(big.verdict["output"], str)
    assert "output_clipped" not in next(i for i in run.items if i.key == "small").verdict
    again = await ev.rescore(run.run_id)
    assert again.verdicts["big"]["passed"] is False
    assert "clipped" in again.verdicts["big"]["error"]
