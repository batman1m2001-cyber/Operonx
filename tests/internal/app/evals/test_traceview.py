"""`TraceView`: one shape for a run, live or stored.

The gate (T4 P2): a view built from a live ``WorkflowTrace`` equals the
view built from the same run read back from a store — the files and the
sqlite store — on a graph with a subgraph, a branch and a real ``LLMOp``
answering a local stand-in model (``_flows.py``). The helpers are checked
against hand counts, and the totals against the store's own summary of
the run.
"""

from __future__ import annotations

import json

import pytest

from operonx.app.evals import ToolCall, TraceView
from operonx.core import Operon
from operonx.telemetry.runs.files import FilesRunStore
from operonx.telemetry.runs.sqlite import SqliteRunStore
from tests.internal.app.evals._fake_llm import PRICE_IN, PRICE_OUT, USAGE
from tests.internal.app.evals._flows import flow


def _store(backend, tmp_path):
    if backend == "files":
        return FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    return SqliteRunStore(path=tmp_path / "runs.sqlite")


async def _run(store, text="lookup order 42"):
    engine = Operon(flow, params={"text": None}, trace=[store] if store is not None else [])
    handle = engine.start(inputs={"text": text})
    await handle.result()
    await handle.collect()  # consumers have run
    return handle.trace


# ── the golden equivalence ───────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["files", "sqlite"])
async def test_a_live_trace_and_its_stored_run_are_the_same_view(tmp_path, llm, backend):
    store = _store(backend, tmp_path)
    trace = await _run(store)
    live = TraceView.from_trace(trace)
    record = store.get_run(trace.trace_id)
    assert record is not None

    stored = TraceView.from_rows(record.nodes, record.meta)
    assert live == stored
    assert TraceView.from_record(record) == live
    assert TraceView.from_store(store, trace.trace_id) == live

    # not equal by being empty: every shape the graph has is in the view
    assert [r.op_name for r in live.rows] == [
        "classify",
        "route",
        "lookup_order",
        "reply",
        "run_tool",
    ]
    assert {r.op_type for r in live.rows} == {"code", "branch", "llm"}
    assert live.metadata == json.loads(json.dumps(trace.metadata, default=str))
    # and value for value: a row's inputs and outputs are what the store holds
    for got, row in zip(stored.rows, record.nodes):
        assert (got.inputs, got.outputs, got.ctx) == (
            row["inputs"],
            row["outputs"],
            tuple(row["ctx"]),
        )

    # the view's numbers are the store's numbers
    s = record.summary
    assert (live.cost_usd, live.unpriced, live.tokens_in, live.tokens_out) == (
        s.cost_usd,
        s.unpriced,
        s.tokens_in,
        s.tokens_out,
    )
    assert len(live.llm_calls()) == s.llm_calls == 1
    assert live.duration_ms == pytest.approx(s.duration_ms)


async def test_a_view_from_a_live_trace_builds_its_rows_on_first_read(tmp_path, llm, monkeypatch):
    from operonx.app.evals import traceview

    trace = await _run(None)
    built = []
    real = traceview.rows_of_trace
    monkeypatch.setattr(
        traceview, "rows_of_trace", lambda *a, **k: built.append(1) or real(*a, **k)
    )
    view = TraceView.from_trace(trace)
    assert view.trace_id == trace.trace_id and built == []
    assert len(view.rows) == 5 and built == [1]
    view.path()
    view.tool_calls()
    assert built == [1]  # once


# ── helpers, against hand counts ─────────────────────────────────────────


async def test_the_helpers_read_the_run(tmp_path, llm):
    view = TraceView.from_trace(await _run(None))

    # routing (branch) and containers are not steps of the path
    assert view.path() == ["classify", "lookup_order", "reply", "run_tool"]
    assert view.path(types=["code"]) == ["classify", "lookup_order", "run_tool"]
    assert view.path(types=["branch"]) == ["route"]

    assert [r.op_name for r in view.ops(under="handle")] == ["classify", "route", "lookup_order"]
    assert [r.op_name for r in view.ops(type="llm")] == ["reply"]
    assert [r.op_name for r in view.ops("classify")] == ["classify"]
    assert view.ops(status="error") == view.errors() == []
    assert view.first("reply") is view.last("reply") is view.ops("reply")[0]
    assert view.first("nope") is None and view.last("nope") is None
    assert view.ops("handle.classify") == view.ops("classify")  # a dotted tail of the full name

    (call,) = view.llm_calls()
    assert call.op_name == "reply" and call.cost_usd == pytest.approx(
        USAGE["prompt_tokens"] * PRICE_IN + USAGE["completion_tokens"] * PRICE_OUT
    )
    assert (call.tokens_in, call.tokens_out) == (12, 4)

    assert view.tool_calls() == [
        ToolCall(
            name="lookup",
            args={"order_id": "42"},
            id="call_0",
            op_id=call.op_id,
            result="shipped",
            status="success",
        )
    ]
    assert view.cost_usd == pytest.approx(2e-05)
    assert (view.tokens_in, view.tokens_out, view.tokens) == (12, 4, 16)
    assert view.duration_ms > 0


async def test_the_other_arm_has_its_own_path_and_no_tool_calls(tmp_path, llm):
    view = TraceView.from_trace(await _run(None, text="hello"))
    assert view.path() == ["classify", "small_talk", "reply", "run_tool"]
    assert view.tool_calls() == []
    assert len(view.llm_calls()) == 1


def _row(name, start, *, op_type="code", outputs=None, status="ok", error=None, ctx=("main",)):
    return {
        "op_id": f"g.{name}#{'.'.join(ctx)}",
        "op_name": name,
        "op_full_name": f"g.{name}",
        "ctx": list(ctx),
        "start_time": start,
        "end_time": start + 0.01,
        "wall_start": 1000.0 + start,
        "duration_ms": 10.0,
        "op_type": op_type,
        "is_yield": False,
        "status": status,
        "error": error,
        "inputs": {},
        "outputs": outputs or {},
        "upstreams": [],
    }


def test_rows_are_ordered_by_start_and_errors_are_found():
    rows = [
        _row("b", 2.0),
        _row("a", 1.0),
        _row("bad", 3.0, status="error", error="Traceback…\nValueError: no"),
    ]
    view = TraceView.from_rows(rows, {"trace_id": "t1", "metadata": {"origin": "eval"}})
    assert view.path() == ["a", "b", "bad"]
    assert [r.op_name for r in view.errors()] == ["bad"]
    assert view.errors()[0].error.endswith("ValueError: no")
    assert view.trace_id == "t1" and view.metadata == {"origin": "eval"}


def test_streaming_token_frames_are_not_llm_calls_and_flat_tool_calls_read():
    frames = [
        _row(
            "talk",
            1.0 + i / 100,
            op_type="llm",
            outputs={"content": w, "final": False},
            ctx=("main", f"[{i}]"),
        )
        for i, w in enumerate(["a", "b"])
    ]
    final = _row(
        "talk",
        1.5,
        op_type="llm",
        outputs={
            "content": "a b",
            "final": True,
            "cost_usd": None,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            "tool_calls": [{"id": "x1", "name": "search", "args": {"q": "tea"}}],
        },
        ctx=("main", "[2]"),
    )
    view = TraceView.from_rows([*frames, final], {"trace_id": "t2"})
    assert [r.outputs["content"] for r in view.llm_calls()] == ["a b"]
    assert view.cost_usd is None and view.unpriced == 1  # unknown, not free
    assert view.tool_calls() == [
        ToolCall(name="search", args={"q": "tea"}, id="x1", op_id=final["op_id"])
    ]


def test_unparseable_tool_arguments_are_kept_as_text():
    llm = _row(
        "reply",
        1.0,
        op_type="llm",
        outputs={
            "cost_usd": 0.0,
            "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{not json"}}],
        },
    )
    (call,) = TraceView.from_rows([llm], {"trace_id": "t3"}).tool_calls()
    assert call.name == "f" and call.args == "{not json"


def test_from_store_says_when_the_run_is_not_there(tmp_path):
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    with pytest.raises(LookupError, match="no run 'missing'"):
        TraceView.from_store(store, "missing")


# ── R2: retried attempts and child executions ───────────────────────────

from operonx import END, START, Retry, child, graph, op  # noqa: E402

_TRIES = {"n": 0}


@op(retry=Retry(max_attempts=2, initial=0.01, on=(ConnectionError,)))
async def agent(text: str = "") -> dict:
    _TRIES["n"] += 1
    async with child("model", inputs={"text": text}, op_type="llm") as m:
        m.outputs = {
            "content": "",
            "cost_usd": 0.001,
            "tool_calls": [{"id": f"c{_TRIES['n']}", "name": "lookup", "args": {"n": _TRIES["n"]}}],
        }
    if _TRIES["n"] == 1:
        async with child("lookup", op_type="tool"):
            raise ConnectionError("blip")
    return {"reply": "done"}


@op
def answer(reply: str = "") -> dict:
    return {"text": reply}


@graph
def retried_agent(text: str = ""):
    a = agent(text=text)
    s = answer(reply=a["reply"])
    START >> a >> s >> END


@pytest.mark.parametrize("backend", ["live", "files"])
async def test_trajectory_reads_skip_a_retried_attempt_and_what_ran_under_it(tmp_path, backend):
    _TRIES["n"] = 0
    store = None if backend == "live" else _store("files", tmp_path)
    handle = Operon(retried_agent, params={"text": None}, trace=[store] if store else []).start(
        {"text": "x"}
    )
    await handle.collect()
    view = (
        TraceView.from_trace(handle.trace)
        if store is None
        else TraceView.from_store(store, handle.trace.trace_id)
    )
    # every execution is still there to read, with its attempt
    assert sorted((r.op_name, r.attempt) for r in view.rows) == [
        ("a", 1), ("a", 2), ("lookup", 1), ("model", 1), ("model", 2), ("s", 1),
    ]  # fmt: skip
    # the graph's path: one a, no retried attempt, no steps inside an op
    assert view.path() == ["a", "s"]
    assert view.path(children=True) == ["a", "model", "s"]  # an op starts before its steps
    assert view.errors() == [], "a retried attempt and its failed step are not errors"
    assert [c.args for c in view.tool_calls()] == [{"n": 2}], "the attempt that counted"
    assert len(view.llm_calls()) == 2, "both calls were made and paid for"
    assert view.ops("model")[0].is_child and not view.ops("a")[0].is_child
    assert view.summary.status == "ok"


# ── a whole run's input and output (online evaluators read these) ────────


@op
def shout(text: str) -> dict:
    return {"loud": text.upper()}


@op
def wrap(loud: str) -> dict:
    return {"reply": f"<{loud}>"}


@graph
def g(text):
    s = shout(text=text)
    w = wrap(loud=s["loud"])
    START >> s >> w >> END


def test_a_runs_input_is_its_first_ops_inputs_and_its_output_its_last_ops_outputs(tmp_path):
    from operonx import END, START, graph, op

    store = _store("files", tmp_path)
    import asyncio

    async def go():
        engine = Operon(g, params={"text": None}, trace=[store])
        handle = engine.start(inputs={"text": "hi"})
        await handle.result()
        await handle.collect()
        return handle.trace

    trace = asyncio.run(go())
    for view in (TraceView.from_trace(trace), TraceView.from_store(store, trace.trace_id)):
        assert view.input == {"text": "hi"}
        assert view.output == {"reply": "<HI>"}


def test_a_source_ops_output_is_what_came_in():
    """A run that starts at an op with no inputs (a door's ingress) took
    in what that op put out."""
    rows = [
        _row("ingress", 1.0, outputs={"item": {"q": "where is my order"}}),
        _row("answer", 2.0, outputs={"text": "on its way"}),
        _row("egress", 3.0, outputs={"item": "on its way"}),
    ]
    view = TraceView.from_rows(rows, {"trace_id": "t9"})

    assert view.input == {"item": {"q": "where is my order"}}
    assert view.output == {"item": "on its way"}


def test_steps_inside_ops_are_not_the_runs_ends():
    rows = [
        _row("a", 1.0, outputs={"x": 1}),
        {**_row("inner", 1.5, outputs={"y": 2}), "op_full_name": "g.sub.inner"},
        _row("b", 2.0, outputs={"z": 3}),
        {**_row("late", 3.0, outputs={"w": 4}), "op_full_name": "g.sub.late"},
    ]
    view = TraceView.from_rows(rows, {"trace_id": "t10"})

    assert view.output == {"z": 3}
    assert TraceView.from_rows([], {"trace_id": "t11"}).output is None
