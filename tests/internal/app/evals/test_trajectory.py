"""Trajectory, tool-call, op-output and budget evaluators (EVALS_PLAN D26, D27).

Table tests of AgentEvals' four match modes over op paths and tool calls —
each mode's passing and failing rows, repeats counted as a multiset — the
case a greedy matcher gets wrong, argument matching (exact / subset /
ignore), the reference taken from the case, ``op_output``'s blame, and
``budget``'s limits, including a cost nobody measured. One end-to-end
eval runs them on a real graph (``_flows.py``).
"""

from __future__ import annotations

import json

import pytest

from operonx.app.evals import Eval, TraceView, budget, exact, trajectory
from operonx.app.evals.evaluators import judge_all, prepare
from tests.internal.app.evals._flows import flow


def _row(i, name, *, op_type="code", outputs=None, ms=10.0):
    return {
        "op_id": f"g.{name}#main.{i}",
        "op_name": name,
        "op_full_name": f"g.{name}",
        "ctx": ["main"],
        "start_time": float(i),
        "end_time": float(i) + ms / 1000,
        "wall_start": 1000.0 + i,
        "duration_ms": ms,
        "op_type": op_type,
        "is_yield": False,
        "status": "ok",
        "error": None,
        "inputs": {},
        "outputs": outputs or {},
        "upstreams": [],
    }


def _path_view(names):
    return TraceView.from_rows([_row(i, n) for i, n in enumerate(names)], {"trace_id": "t"})


def _calls_view(calls, *, cost=0.001, usage=(10, 5), duration_ms=None):
    raw = [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)
    ]
    llm = _row(
        0,
        "reply",
        op_type="llm",
        outputs={
            "tool_calls": raw,
            "cost_usd": cost,
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
        },
    )
    meta = {"trace_id": "t"}
    if duration_ms is not None:
        meta["duration_ms"] = duration_ms
    return TraceView.from_rows([llm], meta)


async def _judge(ev, **avail):
    (verdict,) = (await judge_all([prepare(ev)], avail)).values()
    return verdict


REF = ["a", "b", "c"]


@pytest.mark.parametrize(
    "mode,path,ok",
    [
        ("strict", ["a", "b", "c"], True),
        ("strict", ["a", "c", "b"], False),  # order matters
        ("strict", ["a", "b"], False),
        ("strict", ["a", "b", "c", "d"], False),
        ("unordered", ["c", "a", "b"], True),
        ("unordered", ["a", "b"], False),
        ("unordered", ["a", "b", "c", "c"], False),  # one c too many
        ("subset", ["a", "c"], True),  # nothing beyond the reference
        ("subset", [], True),
        ("subset", ["a", "d"], False),
        ("subset", ["a", "a"], False),  # the reference has one a
        ("superset", ["x", "a", "b", "y", "c"], True),  # at least the reference
        ("superset", ["c", "b", "a", "a"], True),
        ("superset", ["a", "b"], False),
    ],
)
async def test_the_four_modes_over_op_paths(mode, path, ok):
    verdict = await _judge(trajectory.ops(REF, mode=mode), trace=_path_view(path))
    assert verdict["passed"] is ok, verdict
    if not ok:
        assert verdict["reason"]


async def test_scores_are_the_share_of_the_reference_matched():
    v = await _judge(trajectory.ops(REF, mode="superset"), trace=_path_view(["a", "c"]))
    assert v["score"] == pytest.approx(2 / 3, abs=1e-4) and "missing ['b']" in v["reason"]
    v = await _judge(trajectory.ops(REF, mode="subset"), trace=_path_view(["a", "d"]))
    assert v["score"] == pytest.approx(1 / 2) and "unexpected ['d']" in v["reason"]


@pytest.mark.parametrize(
    "args,ref_args,ok",
    [
        ("exact", {"order_id": "42"}, True),
        ("exact", {"order_id": "43"}, False),
        ("exact", {}, False),  # an empty dict is a reference too
        ("subset", {}, True),
        ("subset", {"order_id": "42"}, True),  # extra actual args are allowed
        ("subset", {"order_id": "43"}, False),
        ("subset", {"order_id": "42", "rush": True}, False),
        ("ignore", {"order_id": "nope"}, True),
    ],
)
async def test_tool_call_arguments(args, ref_args, ok):
    view = _calls_view([("lookup", {"order_id": "42", "verbose": False})])
    if args == "exact" and ref_args == {"order_id": "42"}:
        view = _calls_view([("lookup", {"order_id": "42"})])
    ev = trajectory.tool_calls([{"name": "lookup", "args": ref_args}], mode="strict", args=args)
    assert (await _judge(ev, trace=view))["passed"] is ok


async def test_matching_is_maximal_not_greedy():
    # reference 0 accepts either call, reference 1 only the first: a greedy
    # matcher gives the first call to reference 0 and fails reference 1
    view = _calls_view([("f", {"x": 1}), ("f", {"x": 2})])
    ref = [{"name": "f", "args": {}}, {"name": "f", "args": {"x": 1}}]
    for mode in ("unordered", "superset", "subset"):
        ev = trajectory.tool_calls(ref, mode=mode, args="subset")
        assert (await _judge(ev, trace=view))["passed"] is True, mode


async def test_a_reference_entry_without_args_matches_on_the_name():
    view = _calls_view([("lookup", {"order_id": "42"}), ("refund", {"amount": 3})])
    ev = trajectory.tool_calls(["lookup", {"name": "refund"}], mode="strict", args="exact")
    assert (await _judge(ev, trace=view))["passed"] is True
    ev = trajectory.tool_calls(["refund", "lookup"], mode="strict")
    assert (await _judge(ev, trace=view))["passed"] is False


async def test_the_reference_comes_from_the_case_and_its_absence_is_an_error():
    row = {"trajectory": {"ops": ["a", "b"], "tool_calls": [{"name": "lookup"}]}}
    assert (await _judge(trajectory.ops(mode="strict"), trace=_path_view(["a", "b"]), row=row))[
        "passed"
    ]
    assert (
        await _judge(
            trajectory.tool_calls(mode="unordered"), trace=_calls_view([("lookup", {})]), row=row
        )
    )["passed"]
    v = await _judge(trajectory.ops(mode="strict"), trace=_path_view(["a"]), row={})
    assert v["passed"] is False and "trajectory.ops" in v["error"]
    v = await _judge(trajectory.ops(REF), trace=None, row={})
    assert v["passed"] is False and "trace" in v["error"]


def test_bad_modes_are_refused_at_once():
    with pytest.raises(ValueError, match="mode"):
        trajectory.ops(REF, mode="loose")
    with pytest.raises(ValueError, match="args"):
        trajectory.tool_calls([], args="partial")
    with pytest.raises(ValueError, match="at"):
        trajectory.op_output("x", exact(), at="middle")
    with pytest.raises(ValueError, match="at least one"):
        budget()


# ── op_output ─────────────────────────────────────────────────────────────


async def test_op_output_judges_one_op_and_blames_it():
    rows = [
        _row(0, "classify", outputs={"label": "refund"}),
        _row(1, "classify", outputs={"label": "other"}),
    ]
    view = TraceView.from_rows(rows, {"trace_id": "t"})
    expected = {"label": "other"}
    last = await _judge(
        trajectory.op_output("classify", exact("label")), trace=view, expected=expected
    )
    assert last["passed"] is True and last["op"] == "g.classify#main.1"
    first = await _judge(
        trajectory.op_output("classify", exact("label"), at="first"), trace=view, expected=expected
    )
    assert first["passed"] is False and first["op"] == "g.classify#main.0"
    assert "got 'refund'" in first["reason"]
    missing = await _judge(trajectory.op_output("nope", exact()), trace=view, expected=expected)
    assert missing["passed"] is False and "did not run" in missing["reason"]
    assert (
        trajectory.op_output("classify", exact("label")).eval_name
        == "op_output(classify:exact(label))"
    )


async def test_op_output_runs_an_async_check():
    async def positive(output=None):
        return output["n"] > 0

    view = TraceView.from_rows([_row(0, "count", outputs={"n": 3})], {"trace_id": "t"})
    v = await _judge(trajectory.op_output("count", positive), trace=view)
    assert v["passed"] is True and v["op"] == "g.count#main.0"


# ── budget ─────────────────────────────────────────────────────────────────


async def test_budget_limits_are_inclusive_and_say_what_they_measured():
    view = _calls_view([], cost=0.002, usage=(100, 50), duration_ms=1500.0)
    ok = await _judge(budget(ms=1500, cost_usd=0.002, tokens=150, llm_calls=1), trace=view)
    assert ok["passed"] is True
    assert ok["measured"] == {"ms": 1500.0, "cost_usd": 0.002, "tokens": 150, "llm_calls": 1}
    over = await _judge(budget(ms=1499, tokens=149, llm_calls=0), trace=view)
    assert over["passed"] is False
    assert "ms 1500 > 1499" in over["reason"] and "tokens 150 > 149" in over["reason"]
    assert "llm_calls 1 > 0" in over["reason"]


async def test_a_cost_nobody_measured_does_not_pass_a_cost_budget():
    view = _calls_view([], cost=None)
    v = await _judge(budget(cost_usd=1.0), trace=view)
    assert v["passed"] is False and "unpriced" in v["reason"]
    assert (await _judge(budget(llm_calls=5), trace=view))["passed"] is True
    mixed = TraceView.from_rows(
        [
            _row(0, "a", op_type="llm", outputs={"cost_usd": 0.0001}),
            _row(1, "b", op_type="llm", outputs={"cost_usd": None}),
        ],
        {"trace_id": "t"},
    )
    assert mixed.cost_usd == 0.0001 and mixed.unpriced == 1  # a partial sum…
    assert (await _judge(budget(cost_usd=1.0), trace=mixed))["passed"] is False  # …is not the cost
    free = TraceView.from_rows([_row(0, "noop")], {"trace_id": "t"})  # no LLM: costs nothing
    assert (await _judge(budget(cost_usd=0.0), trace=free))["passed"] is True


# ── end to end ─────────────────────────────────────────────────────────────


async def test_an_eval_checks_the_trajectory_of_each_case(tmp_path, llm):
    rows = [
        {
            "id": "order",
            "input": "lookup order 42",
            "trajectory": {
                "ops": ["classify", "lookup_order", "reply", "run_tool"],
                "tool_calls": [{"name": "lookup", "args": {"order_id": "42"}}],
            },
        },
        {
            "id": "chat",
            "input": "hello",
            "trajectory": {"ops": ["classify", "lookup_order"], "tool_calls": []},
        },
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    ev = Eval(
        "trajectories",
        graph=flow,
        input="text",
        dataset=path,
        evaluators=[
            trajectory.ops(mode="strict"),
            trajectory.ops(
                ["classify", "lookup_order"], mode="superset", types=["code"], name="code ops"
            ),
            trajectory.tool_calls(mode="strict", args="exact"),
            trajectory.op_output(
                "classify", lambda output=None: output["kind"] in ("order", "chat")
            ),
            budget(llm_calls=1, cost_usd=0.001, tokens=16),
        ],
        record_dir=tmp_path / "evals",
        trace=[],
    )
    run = await ev.run()
    by = {i.key: i.verdict for i in run.items}  # recorded as they finish
    order, chat = by["order"], by["chat"]
    assert order["passed"] is True, order["checks"]
    assert chat["checks"]["trajectory.ops(strict)"]["passed"] is False  # small_talk, not lookup
    assert chat["checks"]["code ops"]["passed"] is False
    assert chat["checks"]["trajectory.tool_calls(strict, args=exact)"]["passed"] is True
    assert order["checks"]["op_output(classify:<lambda>)"]["op"].endswith("handle.classify#main")
    assert run.meta["eval"]["checks"]["budget"] == {"passed": 2, "cases": 2}
