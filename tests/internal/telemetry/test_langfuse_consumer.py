"""LangfuseConsumer — the ctx tree, ids, clocks and event types.

The tree is asserted on REAL runs (a nested-generator graph, a transient
stream, a sub-graph) so the rules are tested against what the scheduler
actually records, plus hand-built traces for the LLM and error shapes.
A `FakeLangfuseClient` records the batch; no network.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.workflow_trace import STATUS_ERROR, OpExecution, WorkflowTrace
from operonx.telemetry.consumers.langfuse import LangfuseConsumer, build_tree


@dataclass
class FakeLangfuseClient:
    calls: List[List[Dict[str, Any]]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def ingest(self, batch, timeout: int = 30) -> Dict[str, Any]:
        self.calls.append(list(batch))
        return {"successes": [], "errors": list(self.errors)}

    def trace_url(self, trace_id: str) -> str:
        return f"https://langfuse.test/trace/{trace_id}"


def _get(tree, suffix: str):
    """The one node whose id ends with `suffix` — the root graph's name is
    a random hex when the engine is built inline, so ids are matched by
    their tail."""
    hits = [n for k, n in tree.items() if k.endswith(suffix)]
    assert len(hits) == 1, (suffix, sorted(tree))
    return hits[0]


def _run(engine: Operon, trace_id: str) -> WorkflowTrace:
    async def go():
        handle = engine.start(inputs={}, trace_id=trace_id)
        await handle.result()
        return handle.trace

    return asyncio.run(go())


# ── real runs ───────────────────────────────────────────────────────────


@op
async def chunks(n: int):
    for i in range(n):
        await asyncio.sleep(0.003)
        yield {"chunk": f"c{i}"}


@op
def measure(chunk: str):
    return {"size": len(chunk)}


@op
async def tokens(chunk: str):
    for j in range(2):
        await asyncio.sleep(0.002)
        yield {"token": f"{chunk}.t{j}"}


@op
def ack(token: str):
    return {"done": token}


@graph
def nested(n):
    c = chunks(n=n)
    m = measure(chunk=c["chunk"])
    t = tokens(chunk=c["chunk"])
    a = ack(token=t["token"])
    m["size"] >> PARENT["size"]
    a["done"] >> PARENT["done"]
    START >> c >> m >> END
    c >> t >> a >> END


class TestNestedGenerators:
    @pytest.fixture(scope="class")
    def tree(self):
        return build_tree(_run(Operon(nested, params={"n": 2}), "nest"))

    def test_level_one_yields_are_top_level_and_named_with_their_index(self, tree):
        tops = sorted(n["name"] for n in tree.values() if n["parent"] is None)
        assert tops == ["c [0]", "c [1]"]
        assert all(n["rule"] == "level-1 yield" for n in tree.values() if n["parent"] is None)

    def test_inner_yield_hangs_under_the_outer_yield_that_fed_it(self, tree):
        t00 = _get(tree, ".t#main.[0].[0]")
        assert t00["name"] == "t [0]"
        assert t00["parent"] == _get(tree, ".c#main.[0]")["id"]
        assert t00["rule"] == "fed by it"

    def test_batch_op_on_inner_yield_hangs_under_that_yield(self, tree):
        a = _get(tree, ".a#main.[1].[1]")
        assert a["name"] == "a" and a["parent"] == _get(tree, ".t#main.[1].[1]")["id"]

    def test_batch_op_on_outer_yield_is_a_sibling_of_the_inner_generator(self, tree):
        c0 = _get(tree, ".c#main.[0]")["id"]
        assert _get(tree, ".m#main.[0]")["parent"] == c0
        assert _get(tree, ".t#main.[0].[0]")["parent"] == c0

    def test_no_synthetic_node_when_every_yield_is_recorded(self, tree):
        assert all(n["kind"] == "record" for n in tree.values())
        assert len(tree) == 2 + 2 + 4 + 4


@op(transient=True)
async def stream(n: int):
    for i in range(n):
        await asyncio.sleep(0.002)
        yield {"item": i}


@op
def handle(item: int):
    return {"seen": item}


@graph
def transient_flow(n):
    s = stream(n=n)
    h = handle(item=s["item"])
    h["seen"] >> PARENT["seen"]
    START >> s >> h >> END


class TestTransientStream:
    def test_unrecorded_yields_get_one_stand_in_each(self):
        tree = build_tree(_run(Operon(transient_flow, params={"n": 3}), "tr"))
        standins = {n["name"]: n for n in tree.values() if n["kind"] == "stand-in"}
        assert sorted(standins) == ["s [0]", "s [1]", "s [2]"]
        handled = [n for n in tree.values() if n["kind"] == "record" and n["name"] == "h"]
        assert len(handled) == 3
        assert {h["parent"] for h in handled} == set(standins[k]["id"] for k in standins)
        # the stream's own summary record stays at the root
        summary = _get(tree, ".s#main")
        assert summary["parent"] is None and summary["kind"] == "record"


@op
def inner_a(x: int):
    return {"y": x + 1}


@op
def inner_b(y: int):
    return {"z": y * 2}


@graph
def sub(x):
    a = inner_a(x=x)
    b = inner_b(y=a["y"])
    b["z"] >> PARENT["z"]
    START >> a >> b >> END


@op
def feed(chunk: str):
    return {"x": len(chunk)}


@op
def use(z: int):
    return {"out": z}


@graph
def with_subgraph(n):
    c = chunks(n=n)
    f = feed(chunk=c["chunk"])
    s = sub(x=f["x"])
    u = use(z=s["z"])
    u["out"] >> PARENT["out"]
    START >> c >> f >> s >> u >> END


class TestGraphContainer:
    def test_members_nest_under_one_container_per_graph_and_ctx(self):
        tree = build_tree(_run(Operon(with_subgraph, params={"n": 1}), "sg"))
        containers = [n for n in tree.values() if n["kind"] == "container"]
        assert len(containers) == 1 and containers[0]["name"] == "s"
        cid = containers[0]["id"]
        a = _get(tree, ".s.a#main.[0]")
        b = _get(tree, ".s.b#main.[0]")
        assert a["parent"] == cid, "first member hangs under the container"
        assert b["parent"] == a["id"], "a member fed by a sibling keeps that sibling"
        # the member's input crosses the graph boundary, so its upstream names
        # the GraphOp (never a record) and the container lands under the
        # yield of its ctx — a sibling of the feeder, not its child
        assert containers[0]["parent"] == _get(tree, ".c#main.[0]")["id"]
        # and it spans its members
        assert containers[0]["start"] <= a["start"] and containers[0]["end"] >= b["end"]


# ── the batch ───────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return FakeLangfuseClient()


def _anchored(nodes, trace_id="t"):
    return WorkflowTrace(trace_id, "w", started_at=100.0, ended_at=101.0, nodes=nodes,
                         wall_started_at=1_800_000_000.0, metadata={"user_id": "u", "session_id": "s"})


def _rec(op_id, name, start, end, ctx=("main",), **kw):
    return OpExecution(op_id=op_id, op_name=name, op_full_name=f"engine.{name}", ctx=ctx,
                       start_time=start, end_time=end, inputs=kw.pop("inputs", {}),
                       outputs=kw.pop("outputs", {}), **kw)


class TestBatch:
    def test_ids_are_scoped_by_run_and_dates_are_wall_clock(self, client):
        trace = _anchored([_rec("engine.a#main", "a", 100.0, 100.5)], trace_id="run-7")
        LangfuseConsumer(config={"client": client, "workflow_name": "callbot"}).consume(trace)
        batch = client.calls[0]
        assert batch[0]["type"] == "trace-create" and batch[0]["body"]["name"] == "callbot"
        assert batch[0]["body"]["timestamp"].startswith("2027-")
        span = batch[1]["body"]
        assert span["id"] == "run-7/engine.a#main"
        assert span["parentObservationId"] is None
        assert span["startTime"] == "2027-01-15T08:00:00.000Z"
        assert span["endTime"].endswith("00.500Z")

    def test_parent_ids_are_scoped_too(self, client):
        run = _run(Operon(nested, params={"n": 1}), "nest-ids")
        LangfuseConsumer(config={"client": client}).consume(run)
        bodies = {e["body"]["id"]: e["body"] for e in client.calls[0] if e["type"] != "trace-create"}
        child = _get(bodies, ".t#main.[0].[0]")
        parent = _get(bodies, ".c#main.[0]")
        assert child["id"].startswith("nest-ids/") and parent["id"].startswith("nest-ids/")
        assert child["parentObservationId"] == parent["id"]
        assert child["metadata"]["is_yield"] is True and child["metadata"]["ctx"] == "main.[0].[0]"
        assert child["metadata"]["upstreams"][0]["from"] == parent["id"]

    def test_llm_record_is_a_generation_with_model_and_usage(self, client):
        rec = _rec("engine.ask#main", "ask", 100.0, 100.2, op_type="llm",
                   outputs={"content": "yes", "model_used": "gpt-x",
                            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
        LangfuseConsumer(config={"client": client}).consume(_anchored([rec]))
        ev = client.calls[0][1]
        assert ev["type"] == "generation-create"
        assert ev["body"]["model"] == "gpt-x"
        assert ev["body"]["usage"] == {"input": 10, "output": 2, "total": 12, "unit": "TOKENS"}
        assert ev["body"]["output"]["content"] == "yes"

    def test_errored_record_carries_level_and_message(self, client):
        rec = _rec("engine.boom#main", "boom", 100.0, 100.1, status=STATUS_ERROR, error="RuntimeError: kaput")
        LangfuseConsumer(config={"client": client}).consume(_anchored([rec]))
        body = client.calls[0][1]["body"]
        assert body["level"] == "ERROR" and "kaput" in body["statusMessage"]

    def test_rejected_events_are_logged(self, client, caplog):
        from operonx.core.loggings import LOGGER

        client.errors = [{"id": "x", "status": 400, "message": "bad span"}]
        # the core logger does not propagate to the root, so listen on it directly
        LOGGER.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING, logger=LOGGER.name):
                LangfuseConsumer(config={"client": client}).consume(_anchored([_rec("engine.a#main", "a", 100.0, 100.5)]))
        finally:
            LOGGER.removeHandler(caplog.handler)
        assert any("rejected" in r.getMessage() and "bad span" in r.getMessage() for r in caplog.records)

    def test_missing_client_raises(self):
        with pytest.raises(ValueError, match="requires a `client`"):
            LangfuseConsumer(config={}).consume(_anchored([]))

    def test_returns_trace_url(self, client):
        assert LangfuseConsumer(config={"client": client}).consume(_anchored([], "t-lin")) \
            == "https://langfuse.test/trace/t-lin"

    def test_parent_strategy_is_accepted_and_ignored(self, client):
        run = _run(Operon(nested, params={"n": 1}), "ps")
        LangfuseConsumer(config={"client": client, "parent_strategy": "root_only"}).consume(run)
        bodies = {e["body"]["id"]: e["body"] for e in client.calls[0] if e["type"] != "trace-create"}
        assert _get(bodies, ".t#main.[0].[0]")["parentObservationId"] == _get(bodies, ".c#main.[0]")["id"]
