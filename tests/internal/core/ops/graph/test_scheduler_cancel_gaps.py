"""Cancellation and fatal-error gaps found by adversarial review (12 Aug).

Every test here reproduces a defect that the 29 existing interrupt tests
walked straight past, because those tests all use `.parallel()` or a
generator — which puts the emitting op *below* the root context and gives
it a task of its own. The default shapes (a plain `def` op, a sequential
edge, a flat graph) were untested, and all three were broken.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op
from operonx.core.ops.graph.task_scheduler import InterruptTargetError

pytestmark = pytest.mark.unit


class TestSelfAtTheRootContext:
    """`Interrupt.SELF` was still `ALL` for any op at the graph root.

    The F2 fix moved the default from `()` to the emitter's ctx. For an op
    running at `("main",)` those are the same total sweep — so a flat graph
    kept the original bug, and the audit that "checked five paths" checked
    five *dispatch* paths while every one of them ran below the root.
    """

    @staticmethod
    def _flat_graph():
        ran: list = []

        @op
        def seed(n: int) -> dict:
            return {"x": n}

        @op
        def guard(x: int):
            ran.append("guard")
            return Interrupt(reason="just my branch, honest")

        @op
        async def sibling(x: int) -> dict:
            await asyncio.sleep(0.05)
            ran.append("sibling")
            return {"out": x * 10}

        with GraphOp(name="flat") as g:
            s = seed(n=PARENT["n"])
            gd = guard(x=s["x"])
            sb = sibling(x=s["x"])
            START >> s
            s >> gd
            s >> sb >> END
        return g, ran

    @pytest.mark.asyncio
    async def test_it_refuses_rather_than_sweeping_the_run(self):
        g, _ran = self._flat_graph()
        with pytest.raises(InterruptTargetError, match="root"):
            await asyncio.wait_for(Operon(g).run(inputs={"n": 3}), timeout=20)

    @pytest.mark.asyncio
    async def test_the_message_names_both_ways_out(self):
        g, _ran = self._flat_graph()
        with pytest.raises(InterruptTargetError) as exc:
            await asyncio.wait_for(Operon(g).run(inputs={"n": 3}), timeout=20)
        text = str(exc.value)
        assert "Interrupt.ALL" in text, "must name the way to end the run"
        assert "generator" in text or "iteration" in text, "and where SELF does apply"

    @pytest.mark.asyncio
    async def test_explicit_all_is_still_allowed_from_the_root(self):
        """Refusing SELF must not refuse the deliberate version."""

        @op
        def seed(n: int) -> dict:
            return {"x": n}

        @op
        def guard(x: int):
            return Interrupt(ctx_to_cancel=Interrupt.ALL, reason="deliberate")

        with GraphOp(name="flat_all") as g:
            s = seed(n=PARENT["n"])
            gd = guard(x=s["x"])
            START >> s >> gd >> END

        handle = Operon(g).start(inputs={"n": 1})
        await asyncio.wait_for(handle.collect(), timeout=20)
        assert handle.interrupts

    @pytest.mark.asyncio
    async def test_a_nested_subgraph_is_exempt(self):
        """A subgraph's root ctx is also `("main",)`, but its sweep runs in
        its own scheduler and cannot reach the parent — bounded by
        construction rather than by the tuple, so SELF stays legal."""
        from operonx.core.ops.graph.graph_op import graph

        @op
        def stopper(x: int):
            return Interrupt(reason="child stops itself")

        @op
        async def slow_sibling(n: int) -> dict:
            await asyncio.sleep(0.1)
            return {"slow": n}

        @op
        def seed(n: int) -> dict:
            return {"x": n}

        @graph
        def child(x):
            s = stopper(x=x)
            START >> s >> END

        with GraphOp(name="nested_exempt") as g:
            sd = seed(n=PARENT["n"])
            sub = child(x=sd["x"])
            slow = slow_sibling(n=sd["x"])
            START >> sd
            sd >> sub
            sd >> slow >> END

        handle = Operon(g).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=20)
        assert "slow" in out, "the parent's sibling must survive"


class TestInlineOpsAreSwept:
    """`@op` on a plain `def` resolves to `bound="sync"`, which the
    scheduler runs from `inline_pending` rather than as a task. `_sweep_ctx`
    cancelled `tasks_by_ctx` and never touched that list, so an `Interrupt`
    in an all-sync graph — the default shape — swept nothing and the run
    completed normally with an interrupt record attached."""

    @pytest.mark.asyncio
    async def test_all_cancels_downstream_sync_ops(self):
        ran: list = []

        @op
        def seed(n: int) -> dict:
            return {"x": n}

        @op
        def first(x: int):
            ran.append("a")
            return Interrupt(ctx_to_cancel=Interrupt.ALL, reason="stop everything")

        @op
        def second(x: int) -> dict:
            ran.append("b")
            return {"out": x}

        @op
        def third(x: int) -> dict:
            ran.append("c")
            return {"out": x}

        with GraphOp(name="all_sync") as g:
            s = seed(n=PARENT["n"])
            a = first(x=s["x"])
            b = second(x=s["x"])
            c = third(x=s["x"])
            START >> s
            s >> a
            s >> b >> END
            s >> c >> END

        handle = Operon(g).start(inputs={"n": 5})
        await asyncio.wait_for(handle.collect(), timeout=20)
        assert ran == ["a"], f"sync ops downstream of an ALL sweep still ran: {ran}"


class TestTheEmittersOwnEofSurvives:
    """A non-generator op enqueues its `Interrupt` and its `EOF` in the same
    event-loop slice, so the sweep found the EOF already queued and dropped
    it — while the sequential-edge section skipped the emitter precisely
    *because* it expected that EOF to do the advance. Nothing advanced:
    `seq_active` stayed True and the remaining items were stranded.
    """

    @pytest.mark.asyncio
    async def test_a_sequential_fanout_finishes_after_an_interrupt(self):
        ran: list = []

        @op
        def source(n: int):
            for i in range(6):
                yield {"i": i}

        @op
        def work(i: int):
            ran.append(i)
            if i == 1:
                return Interrupt(reason="skip just this item")
            return {"j": i}

        # No `.parallel()` — the default sequential policy, which is what
        # every existing interrupt test opts out of.
        with GraphOp(name="seq_sweep") as g:
            s = source(n=PARENT["n"])
            w = work(i=s["i"])
            START >> s >> w >> END

        handle = Operon(g).start(inputs={"n": 1})
        out = await asyncio.wait_for(handle.collect(), timeout=25)
        assert ran == [0, 1, 2, 3, 4, 5], f"items were stranded: {ran}"
        assert out.get("j") == [0, 2, 3, 4, 5]


class TestAFatalErrorDoesNotHang:
    """`ObserveBudgetExceeded` is a `BaseException` by design. `_pump`
    caught only `Exception`, so it escaped without enqueuing anything —
    `inflight` hit zero while the main loop was already parked in
    `await queue.get()`. The run hung forever and the exception was never
    retrieved. Reachable from a plain `run()` since observe_max became
    always-on."""

    @pytest.mark.asyncio
    async def test_an_async_op_over_budget_raises_instead_of_hanging(self):
        from operonx.checkpoint import ObserveBudgetExceeded

        @op(observe_max=1, bound="io")
        async def burst(n: int) -> dict:
            return {"a": 1, "b": 2}

        with GraphOp(name="fatal_async") as g:
            b = burst(n=PARENT["n"])
            START >> b >> END

        with pytest.raises(ObserveBudgetExceeded):
            await asyncio.wait_for(Operon(g).run(inputs={"n": 1}), timeout=10)

    @pytest.mark.asyncio
    async def test_a_sync_op_over_budget_still_raises(self):
        from operonx.checkpoint import ObserveBudgetExceeded

        @op(observe_max=1)
        def burst(n: int) -> dict:
            return {"a": 1, "b": 2}

        with GraphOp(name="fatal_sync") as g:
            b = burst(n=PARENT["n"])
            START >> b >> END

        with pytest.raises(ObserveBudgetExceeded):
            await asyncio.wait_for(Operon(g).run(inputs={"n": 1}), timeout=10)


class TestAReusedInterruptIsNotMutated:
    """Resolving stamped the emitter's ctx onto the caller's object. A
    module-level `STOP = Interrupt(...)` was therefore resolved once, and
    every later emission swept a stale context and reported the wrong
    emitter."""

    @pytest.mark.asyncio
    async def test_two_emissions_of_one_object_target_their_own_contexts(self):
        STOP = Interrupt(reason="shared instance")

        @op
        def source(n: int):
            for i in range(6):
                yield {"i": i}

        @op
        async def work(i: int):
            await asyncio.sleep(0.01)
            if i in (1, 3):
                return STOP
            return {"j": i}

        with GraphOp(name="shared_int") as g:
            s = source(n=PARENT["n"])
            w = work(i=s["i"].parallel())
            START >> s >> w >> END

        handle = Operon(g).start(inputs={"n": 1})
        await asyncio.wait_for(handle.collect(), timeout=25)

        targets = [e.ctx_to_cancel for e in handle.interrupts]
        assert len(set(targets)) == len(targets), (
            f"two emissions shared one resolved target: {targets}"
        )
        assert STOP.ctx_to_cancel is Interrupt.SELF, "the caller's object was mutated"
