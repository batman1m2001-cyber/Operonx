"""F2 — ``Interrupt()`` without a target used to cancel the whole run.

``ctx_to_cancel`` defaulted to ``()``, which is a prefix of every context,
so ``_is_descendant_or_equal`` said yes to all of them. The run came back
as ``{"__interrupt__": …}`` with no error anywhere — omitting one keyword
argument silently discarded everything in flight and looked like success.

Measured on the old default: 8 parallel branches, one of them emitting
``Interrupt(reason=…)``, produced 2 results. It now produces 7 — the
interrupted branch and nothing else.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op
from operonx.core.ops._events import SELF_CTX

pytestmark = pytest.mark.unit


def _fanout_graph(name: str, interrupt_factory):
    """8 parallel branches; branch j==2 emits whatever the factory returns."""

    @op
    def source(n: int):
        for i in range(8):
            yield {"i": i}

    @op
    async def slow(i: int):
        await asyncio.sleep(0.02)
        return {"j": i}

    @op
    def guard(j: int):
        if j == 2:
            return interrupt_factory()
        return {"k": j}

    with GraphOp(name=name) as g:
        s = source(n=PARENT["n"])
        c = slow(i=s["i"].parallel())
        gd = guard(j=c["j"])
        START >> s >> c >> gd >> END
    return g


async def _run(g):
    handle = Operon(g).start(inputs={"n": 8})
    out = await asyncio.wait_for(handle.collect(), timeout=30)
    return out, handle


class TestDefaultTarget:
    def test_the_default_is_the_sentinel_not_the_empty_tuple(self):
        assert Interrupt(reason="x").ctx_to_cancel is SELF_CTX

    def test_the_sentinel_is_not_a_tuple(self):
        """A tuple sentinel would fall through to the old behaviour if any
        path forgot to resolve it — matching every context instead of
        raising. This has to fail loudly."""
        assert not isinstance(SELF_CTX, tuple)

    @pytest.mark.asyncio
    async def test_siblings_survive_an_untargeted_interrupt(self):
        out, _ = await _run(_fanout_graph("f2_default", lambda: Interrupt(reason="oops")))
        assert len(out.get("k", [])) == 7, "only the emitting branch should be lost"

    @pytest.mark.asyncio
    async def test_the_interrupt_is_still_reported(self):
        _, handle = await _run(_fanout_graph("f2_report", lambda: Interrupt(reason="oops")))
        assert len(handle.interrupts) == 1
        assert handle.interrupts[0].reason == "oops"

    @pytest.mark.asyncio
    async def test_the_resolved_target_is_the_emitter_ctx(self):
        _, handle = await _run(_fanout_graph("f2_ctx", lambda: Interrupt(reason="oops")))
        event = handle.interrupts[0]
        assert event.ctx_to_cancel == event.ctx


class TestGeneratorEmitter:
    """A generator's ``SELF`` is the *yield* that emitted it.

    ``_pump`` stamps ``result.ctx`` with the op's dispatch ctx, because
    the self-cancel guard looks that up in ``tasks_by_ctx``. Resolving
    ``SELF`` against the same value made a top-level generator's untargeted
    interrupt cancel everything under ``("main",)`` — F2 all over again,
    just harder to see because earlier yields had already completed. It
    resolves against ``item_ctx`` instead.
    """

    @pytest.mark.asyncio
    async def test_a_mid_stream_interrupt_spares_the_other_yields(self):
        @op
        def gen(n: int):
            for i in range(6):
                if i == 2:
                    yield Interrupt(reason="mid-stream")
                else:
                    yield {"i": i}

        @op
        async def use(i: int):
            await asyncio.sleep(0.01)
            return {"j": i}

        with GraphOp(name="f2_gen") as g:
            s = gen(n=PARENT["n"])
            u = use(i=s["i"].parallel())
            START >> s >> u >> END

        handle = Operon(g).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=30)
        assert len(out.get("j", [])) == 5, "only the interrupting yield should be lost"

    @pytest.mark.asyncio
    async def test_the_target_is_the_yield_ctx_not_the_op_ctx(self):
        @op
        def gen(n: int):
            yield {"i": 0}
            yield Interrupt(reason="x")

        with GraphOp(name="f2_genctx") as g:
            s = gen(n=PARENT["n"])
            START >> s >> END

        handle = Operon(g).start(inputs={"n": 1})
        await asyncio.wait_for(handle.collect(), timeout=30)
        event = handle.interrupts[0]
        assert event.ctx_to_cancel != event.ctx, "the yield ctx is finer than the op ctx"
        assert len(event.ctx_to_cancel) > len(event.ctx)


class TestNestedGraph:
    """A subgraph runs its own scheduler, with `output_queue=None`.

    Two things followed, both silent. The ``__interrupt__`` record was
    dropped, so ``handle.interrupts`` stayed empty and nothing recorded
    that a branch had been cancelled. And ``GraphOp.run`` yielded the
    cells as they stood — all-``None`` — which the parent forwarded as an
    ordinary result. Measured: six branches, one interrupted, and the
    parent reported ``[0, 1, None, 3, 4, 5]`` with zero interrupts.
    """

    @staticmethod
    def _graph(recorder):
        from operonx.core.ops.graph.graph_op import graph

        @op
        def src(n: int):
            for i in range(6):
                yield {"i": i}

        @op
        async def slow(i: int):
            await asyncio.sleep(0.02)
            return {"j": i}

        @op
        def guard(j: int):
            recorder.append(j)
            if j == 2:
                return Interrupt(reason="nested")
            return {"k": j}

        @graph
        def inner(i):
            c = slow(i=i)
            gd = guard(j=c["j"])
            START >> c >> gd >> END

        with GraphOp(name="nested_outer") as g:
            s = src(n=PARENT["n"])
            blk = inner(i=s["i"].parallel())
            START >> s >> blk >> END
        return g

    @pytest.mark.asyncio
    async def test_no_phantom_none_result_for_the_cancelled_branch(self):
        seen: list = []
        handle = Operon(self._graph(seen)).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=30)
        assert sorted(seen) == [0, 1, 2, 3, 4, 5], "every branch should reach the guard"
        assert out["k"] == [0, 1, 3, 4, 5], "the cancelled branch must not report a value"

    @pytest.mark.asyncio
    async def test_the_interrupt_reaches_the_handle(self):
        handle = Operon(self._graph([])).start(inputs={"n": 1})
        await asyncio.wait_for(handle.collect(), timeout=30)
        assert len(handle.interrupts) == 1
        assert handle.interrupts[0].reason == "nested"

    @pytest.mark.asyncio
    async def test_siblings_of_the_cancelled_branch_survive(self):
        handle = Operon(self._graph([])).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=30)
        assert len(out["k"]) == 5


class TestInsideALoop:
    """A back-edge loop runs in the synthetic ``__loop_0__`` subgraph, so
    ``SELF`` has to resolve to the *iteration* ctx — cancelling the run
    would end the loop by destroying it."""

    @pytest.mark.asyncio
    async def test_an_iteration_can_stop_itself(self):
        from operonx.core.ops.flow.branch_op import if_
        from operonx.core.ops.graph.graph_op import graph

        trace: list = []

        @op
        def step(count: int) -> dict:
            trace.append(count)
            if count == 2:
                return Interrupt(reason="stop at 2")
            return {"count": count + 1, "done": count >= 5}

        @graph
        def g():
            PARENT.declare(count=0, done=False)
            s = step(count=PARENT["count"])
            s["count"] >> PARENT["count"]
            s["done"] >> PARENT["done"]
            START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712

        built = g()
        handle = Operon(built).start(inputs={})
        await asyncio.wait_for(handle.collect(), timeout=30)

        assert trace == [0, 1, 2], "the loop must run up to the interrupt, then stop"
        assert len(handle.interrupts) == 1
        event = handle.interrupts[0]
        assert "__loop_0__" in event.ctx_to_cancel[-1], (
            "the target must be the iteration, not the run"
        )


class TestExplicitAll:
    @pytest.mark.asyncio
    async def test_cancelling_everything_still_works_when_asked_for(self):
        """The old default is still reachable — it just has to be written."""
        out, handle = await _run(
            _fanout_graph(
                "f2_all",
                lambda: Interrupt(ctx_to_cancel=Interrupt.ALL, reason="stop"),
            )
        )
        assert len(out.get("k", [])) < 7
        assert handle.interrupts

    def test_all_is_the_empty_tuple(self):
        assert Interrupt.ALL == ()

    @pytest.mark.asyncio
    async def test_an_explicit_target_is_untouched(self):
        target = ("main", "[0]")
        out, handle = await _run(
            _fanout_graph(
                "f2_explicit",
                lambda: Interrupt(ctx_to_cancel=target, reason="one"),
            )
        )
        assert handle.interrupts[0].ctx_to_cancel == target
