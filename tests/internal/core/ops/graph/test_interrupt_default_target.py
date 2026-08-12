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
