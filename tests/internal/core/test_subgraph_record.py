"""A subgraph is a step with values — its own record in the trace.

`GraphOp.run` held a nested graph's inputs, outputs, start, end and error
and recorded none of it: a trace had the ops inside `agent_turn` and an
empty box named after it. The record is ``op_type="subgraph"`` — not
``"graph"``, which is a nested run's root (``operonx.core.nested``) and
reshapes the tree — and the summaries leave it out, because its time,
cost and errors are its children's.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.ops.graph.graph_op import graph
from operonx.core.workflow_trace import STATUS_ERROR, STATUS_OK, SUBGRAPH
from operonx.telemetry.consumers.langfuse import build_tree
from operonx.telemetry.runs.model import row_of, summarize

pytestmark = pytest.mark.unit


@op
def double(x: int) -> dict:
    return {"y": x * 2}


@graph
def inner(x):
    d = double(x=x)
    START >> d >> END


@op
def seed(n: int) -> dict:
    return {"x": n + 1}


@op
def explode(x: int) -> dict:
    raise ValueError("no")


@graph
def broken(x):
    e = explode(x=x)
    START >> e >> END


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@graph
def streamed(n):
    it = items(n=n)
    START >> it >> END


def _run(g, inputs):
    async def go():
        handle = Operon(g).start(inputs=inputs)
        await asyncio.wait_for(handle.collect(), timeout=20)
        return handle.trace

    return asyncio.run(go())


def _outer():
    with GraphOp(name="outer") as g:
        p = seed(n=PARENT["n"])
        sub = inner(x=p["x"])
        START >> p >> sub >> END
    return g


class TestTheRecord:
    def test_a_nested_graph_records_its_values(self):
        trace = _run(_outer(), {"n": 4})
        by = {n.op_full_name: n for n in trace.nodes}
        rec = by["outer.sub"]
        assert rec.op_type == SUBGRAPH
        assert rec.inputs == {"x": 5}
        assert rec.outputs == {"y": 10}
        assert rec.status == STATUS_OK
        # nesting lives in the name: same ctx as the ops inside it
        assert tuple(rec.ctx) == tuple(by["outer.sub.d"].ctx)
        # it ran around them
        assert rec.start_time <= by["outer.sub.d"].start_time
        assert rec.end_time >= by["outer.sub.d"].end_time
        # fed by the op before it, like any op
        assert [u.from_op_full_name for u in rec.upstreams] == ["outer.p"]

    def test_the_root_graph_is_the_run_not_a_record(self):
        trace = _run(_outer(), {"n": 4})
        assert "outer" not in {n.op_full_name for n in trace.nodes}

    def test_a_failure_inside_fails_the_record(self):
        with GraphOp(name="outer_f") as g:
            p = seed(n=PARENT["n"])
            sub = broken(x=p["x"])
            START >> p >> sub >> END
        trace = _run(g, {"n": 1})
        rec = next(n for n in trace.nodes if n.op_full_name == "outer_f.sub")
        assert rec.status == STATUS_ERROR
        assert "outer_f.sub.e" in (rec.error or "")  # names the op that raised

    def test_a_streamed_graph_records_each_item(self):
        with GraphOp(name="outer_s") as g:
            sub = streamed(n=PARENT["n"])
            START >> sub >> END
        trace = _run(g, {"n": 3})
        recs = [n for n in trace.nodes if n.op_full_name == "outer_s.sub"]
        assert [r.outputs for r in recs] == [{"i": 0}, {"i": 1}, {"i": 2}]
        assert all(r.is_yield for r in recs)
        # the inputs are one per run: stored once, the other items point at it
        assert recs[0].inputs == {"n": 3}
        assert all(r.inputs_from == recs[0].op_id for r in recs[1:])


class TestReaders:
    def test_the_tree_keeps_its_shape_and_the_box_gets_the_values(self):
        trace = _run(_outer(), {"n": 4})
        nodes = build_tree(trace)
        boxes = [n for n in nodes.values() if n["kind"] == "container"]
        assert [b["name"] for b in boxes] == ["sub"]
        box = boxes[0]
        assert box["record"] is not None and box["record"].outputs == {"y": 10}
        # the op inside hangs under the box, and the record is not a second node
        d = next(n for n in nodes.values() if n["record"] is not None
                 and n["record"].op_full_name == "outer.sub.d")
        assert d["parent"] == box["id"]
        assert sum(1 for n in nodes.values()
                   if n["record"] is not None and n["record"].op_type == SUBGRAPH) == 1

    def test_summaries_count_the_ops_not_the_box(self):
        trace = _run(_outer(), {"n": 4})
        summary, rollups = summarize("t", [row_of(n, trace) for n in trace.nodes])
        assert sorted(r.op for r in rollups) == ["d", "p"]
        assert summary.executions == 2


class TestStateObjects:
    """An op's state object (a dataclass, a pydantic model) reads as its
    fields in a trace — the callbot's `AgentState` was `$unserializable`
    on every op that carried it, `agent_turn`'s values included."""

    def test_a_dataclass_reads_as_its_fields(self):
        import dataclasses

        import orjson

        from operonx.telemetry.consumer import Consumer
        from operonx.telemetry.media import json_default

        class Probe(Consumer):
            async def consume(self, trace):
                return None

        @dataclasses.dataclass(frozen=True)
        class State:
            current_state: str = "REMINDER"
            should_hangup: bool = False

        want = {"$type": "State", "current_state": "REMINDER", "should_hangup": False}
        assert Probe().sanitize({"s": State()}) == {"s": want}
        text = orjson.dumps({"s": State()}, default=json_default(None, 1024),
                            option=orjson.OPT_PASSTHROUGH_DATACLASS)
        assert orjson.loads(text) == {"s": want}


class TestLangfuseReadBack:
    def test_boxes_are_not_rows_but_a_subgraph_record_is(self):
        from operonx.telemetry.runs.langfuse import records_of_langfuse_trace

        detail = {"observations": [
            {"id": "a", "name": "d", "metadata": {"kind": "record", "op_type": "code",
                                                   "op_full_name": "outer.sub.d"}},
            {"id": "b", "name": "sub", "metadata": {"kind": "container"}},
            {"id": "c", "name": "audio_in [3]", "metadata": {"kind": "stand-in"}},
            {"id": "e", "name": "sub", "metadata": {"kind": "container", "op_type": "subgraph",
                                                     "op_full_name": "outer.sub"}},
        ]}
        rows = list(records_of_langfuse_trace(detail))
        assert [(r["op_id"], r["op_type"]) for r in rows] == [("a", "code"), ("e", "subgraph")]
