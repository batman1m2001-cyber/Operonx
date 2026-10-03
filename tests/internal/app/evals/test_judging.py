"""How an eval hands each case to its evaluators (EVALS_PLAN D22, D23, D28).

Gates: an evaluator that names ``trace`` gets a :class:`TraceView` of the
case's own run; one that does not costs nothing (no view is built); one
taking ``**kwargs`` gets a view that builds nothing until read; the trace
never reaches the record and is let go once the case is judged; async
evaluators of one case run at the same time, and the verdict keeps the
evaluators' order.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from operonx.app.evals import Eval, TraceView, exact
from operonx.app.jobs.record import ItemResult
from tests.internal.app.evals._flows import flow

CASES = [
    {"id": "order", "input": "lookup order 42", "expected": {"tool_message": {"content": "shipped"}}},
    {"id": "chat", "input": "hello", "expected": {"tool_message": {"content": None}}},
]
ANSWER = "tool_message.content"


def _eval(tmp_path: Path, evaluators, rows=CASES, **kw) -> Eval:
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    kw.setdefault("trace", [])
    return Eval(
        "judging",
        graph=flow,
        item_input="text",
        dataset=path,
        evaluators=evaluators,
        record_dir=tmp_path / "evals",
        **kw,
    )


async def test_an_evaluator_that_names_trace_gets_its_cases_run(tmp_path, llm):
    seen = {}

    def took_the_tool_path(output=None, trace=None):
        seen[trace.trace_id] = trace
        return trace.path() == ["classify", "lookup_order", "reply", "run_tool"]

    run = await _eval(tmp_path, [took_the_tool_path]).run()
    by_key = {i.key: i for i in run.items}
    assert by_key["order"].verdict["passed"] is True
    assert by_key["chat"].verdict["passed"] is False  # small_talk's path
    assert set(seen) == {i.trace_id for i in run.items}  # each case's own run
    assert all(isinstance(v, TraceView) for v in seen.values())
    assert [c.name for c in seen[by_key["order"].trace_id].tool_calls()] == ["lookup"]


async def test_evaluators_that_do_not_ask_build_no_view(tmp_path, llm, monkeypatch):
    from operonx.app.evals import traceview

    built = []
    real = traceview.TraceView.from_trace.__func__
    monkeypatch.setattr(
        traceview.TraceView,
        "from_trace",
        classmethod(lambda cls, t: built.append(t) or real(cls, t)),
    )
    run = await _eval(tmp_path, [exact(ANSWER)]).run()
    assert run.meta["eval"]["passed"] == 2 and built == []


async def test_a_kwargs_evaluator_gets_a_view_that_reads_nothing_until_asked(
    tmp_path, llm, monkeypatch
):
    from operonx.app.evals import traceview

    rows_built = []
    real = traceview.rows_of_trace
    monkeypatch.setattr(
        traceview, "rows_of_trace", lambda *a, **k: rows_built.append(1) or real(*a, **k)
    )
    got = []

    def anything(**kw):
        got.append(kw)
        return True

    await _eval(tmp_path, [anything]).run()
    assert {"input", "output", "expected", "row", "outputs", "trace"} <= set(got[0])
    assert isinstance(got[0]["trace"], TraceView) and rows_built == []


async def test_the_trace_never_reaches_the_record(tmp_path, llm):
    seen = []

    def uses_trace(trace=None):
        return bool(trace.rows)

    run = await _eval(tmp_path, [uses_trace], on_item=seen.append).run()
    assert all(r.trace is None for r in seen)  # let go once the case was judged
    lines = (run.path / "items.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and all("trace" not in json.loads(line) for line in lines)
    assert "trace" not in ItemResult("k", "ok", trace=object()).as_dict()
    assert all(i.verdict["passed"] for i in run.items)


async def test_async_evaluators_of_one_case_run_together_in_order(tmp_path, llm):
    def slow(name):
        async def check(output=None):
            await asyncio.sleep(0.3)
            return True

        check.eval_name = name
        return check

    evs = [slow("a"), exact(ANSWER), slow("b"), slow("c")]
    t0 = time.perf_counter()
    run = await _eval(tmp_path, evs, rows=CASES[:1]).run()
    took = time.perf_counter() - t0
    assert took < 0.75, took  # one at a time would take ≥ 0.9 s
    checks = run.items[0].verdict["checks"]
    assert list(checks) == ["a", f"exact({ANSWER})", "b", "c"]
    assert all(checks[n]["ms"] >= 290 for n in ("a", "b", "c"))


async def test_a_failing_evaluator_fails_only_its_check(tmp_path, llm):
    async def broken(trace=None):
        raise RuntimeError("no")

    run = await _eval(tmp_path, [broken, exact(ANSWER)], rows=CASES[:1]).run()
    checks = run.items[0].verdict["checks"]
    assert checks["broken"] == {"passed": False, "error": "RuntimeError: no", "ms": pytest.approx(checks["broken"]["ms"])}
    assert checks[f"exact({ANSWER})"]["passed"] is True
