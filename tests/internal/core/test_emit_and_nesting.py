"""Two §2 claims that were 🟡 for months: `EmitOp` + `stream(mode="custom")`,
and what "sub-agent isolation" actually means.

The first held. The second did not: the plan and `state-model.md` both said
a nested graph's ops "live at a deeper ctx tuple". They run at the *same*
context — the nesting is in the op's **name**. Pinned here because the
wrong model leads somewhere real: you would expect a context boundary to
scope cancellation, and there isn't one.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op
from operonx.core.ops.flow.emit_op import EmitOp
from operonx.core.ops.graph.graph_op import graph

pytestmark = pytest.mark.unit


@op
def steps(n: int):
    for i in range(3):
        yield {"step": i}


@op
def finish(step: int) -> dict:
    return {"done": step}


class TestEmitOp:
    @pytest.mark.asyncio
    async def test_events_reach_a_custom_stream_consumer(self):
        with GraphOp(name="emit") as g:
            w = steps(n=PARENT["n"])
            e = EmitOp(name="progress", channel="progress", inputs={"payload": w["step"]})
            f = finish(step=w["step"])
            START >> w >> e
            w >> f >> END

        got = [evt async for evt in Operon(g).stream({"n": 1}, mode="custom")]
        assert [e.payload for e in got] == [0, 1, 2]
        assert {e.channel for e in got} == {"progress"}

    @pytest.mark.asyncio
    async def test_channels_filters(self):
        with GraphOp(name="emit2") as g:
            w = steps(n=PARENT["n"])
            e1 = EmitOp(name="p1", channel="progress", inputs={"payload": w["step"]})
            e2 = EmitOp(name="p2", channel="debug", inputs={"payload": w["step"]})
            f = finish(step=w["step"])
            START >> w >> e1
            w >> e2
            w >> f >> END

        got = [
            evt async for evt in Operon(g).stream({"n": 1}, mode="custom", channels=["progress"])
        ]
        assert got, "the filter must not silence everything"
        assert {e.channel for e in got} == {"progress"}

    @pytest.mark.asyncio
    async def test_an_emit_inside_a_nested_graph_still_reaches_the_top(self):
        """Subgraphs run their own scheduler, so this is not free — a
        progress event from deep in a sub-agent is exactly the case the
        feature exists for."""

        @graph
        def inner(n):
            w = steps(n=n)
            e = EmitOp(name="deep", channel="progress", inputs={"payload": w["step"]})
            f = finish(step=w["step"])
            START >> w >> e
            w >> f >> END

        with GraphOp(name="emit3") as g:
            sub = inner(n=PARENT["n"])
            START >> sub >> END

        got = [evt async for evt in Operon(g).stream({"n": 1}, mode="custom")]
        assert len(got) == 3


class TestNestedGraphIsolation:
    @staticmethod
    def _built():
        @op
        def child_op(x: int) -> dict:
            return {"y": x * 2}

        @graph
        def child(x):
            c = child_op(x=x)
            START >> c >> END

        @op
        def parent_op(n: int) -> dict:
            return {"x": n + 1}

        with GraphOp(name="outer") as g:
            p = parent_op(n=PARENT["n"])
            sub = child(x=p["x"])
            START >> p >> sub >> END
        return g

    @pytest.mark.asyncio
    async def test_the_nested_graph_computes(self):
        out = await asyncio.wait_for(Operon(self._built()).run(inputs={"n": 4}), timeout=20)
        assert out["y"] == 10

    @pytest.mark.asyncio
    async def test_nesting_lives_in_the_name_not_the_context(self):
        """The claim under test was "child ops live at a deeper ctx tuple".
        They do not. `ctx` carries *iteration* — generator items, loop
        turns — and the graph tree is carried by the dotted op name."""
        handle = Operon(self._built()).start(inputs={"n": 4})
        await asyncio.wait_for(handle.collect(), timeout=20)

        by_name = {n.op_full_name: n.ctx for n in handle.trace.nodes}
        assert "outer.p" in by_name and "outer.sub.c" in by_name
        assert by_name["outer.sub.c"] == by_name["outer.p"] == ("main",)

    def test_a_reference_out_of_a_subgraph_fails_to_build(self):
        """Hermeticity is enforced by name at build time — which is the
        only place it *can* be, given there is no context boundary."""

        @op
        def child_op(x: int) -> dict:
            return {"y": x}

        @op
        def parent_op(n: int) -> dict:
            return {"x": n}

        with pytest.raises(ValueError):
            with GraphOp(name="leaky") as g:
                p = parent_op(n=PARENT["n"])

                @graph
                def leaky(x):
                    bad = child_op(x=p["x"])  # reaches out of the subgraph
                    START >> bad >> END

                sub = leaky(x=p["x"])
                START >> p >> sub >> END

    @pytest.mark.asyncio
    async def test_a_child_interrupt_does_not_take_the_parent_down(self):
        """`Interrupt.SELF` in a subgraph resolves to ("main",) — which
        *looks* like the whole run. It is scoped by the child's own
        scheduler instead, so the parent's siblings survive. The reported
        target is broader than the effect, and that is worth knowing before
        reading a trace."""

        @op
        def seed(n: int) -> dict:
            return {"x": n}

        @op
        def stopper(x: int) -> dict:
            return Interrupt(reason="child stops itself")

        @op
        async def slow_sibling(n: int) -> dict:
            await asyncio.sleep(0.15)
            return {"slow": n}

        @graph
        def child(x):
            s = stopper(x=x)
            START >> s >> END

        with GraphOp(name="nest_int") as g:
            sd = seed(n=PARENT["n"])
            sub = child(x=sd["x"])
            slow = slow_sibling(n=sd["x"])
            START >> sd
            sd >> sub
            sd >> slow >> END

        handle = Operon(g).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=20)
        assert "slow" in out, "a sibling still in flight must survive"
        assert len(handle.interrupts) == 1
