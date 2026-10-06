"""A back-edge source below a generator fan-out must still fire.

`STATE_LOOP_REFACTOR_PLAN.md:518` specified loop termination as: the
back-edge source has an ``end_time`` cell **at the iteration's ctx**.
That assumes every back-edge source runs at the loop's own context. It
does not: downstream of a generator an op runs at ``(…, "[i]")``, and
behind ``Ref.collect()`` at ``(…, "[i]", "__collect__")``.

The exact-match rule therefore reported "did not fire" on the first
iteration and every such loop stopped after one pass — silently, with no
error, which is how it reached production. None of the 32 existing
cycle-rewrite tests put a generator inside a loop.

Iteration contexts are *siblings* tagged ``#N`` rather than nested, which
is what makes matching descendants safe: iteration 1's ctx
``("main", "g.__loop_0__#1")`` does not contain iteration 0's
``("main", "[0]")``.

Termination is now decided from the frames the loop's own scheduler
routes (a back-edge fires when its source emits a frame along it), which
sees a source at any depth below the iteration without matching contexts.
These tests keep the end-to-end shapes that broke the old rule.
"""

from __future__ import annotations

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_

pytestmark = pytest.mark.unit


# ── end-to-end: the shape that was capped at one iteration ──────────────


@op
def emit_items(n: int = 0):
    """Generator — the thing that pushes downstream ops to item contexts."""
    for i in range(max(0, n)):
        yield {"item": i}


@op
def double(item: int = 0) -> dict:
    return {"doubled": item * 2}


@op
def step(count: int = 0) -> dict:
    count = (count or 0) + 1
    return {"count": count, "done": count >= 3, "width": 2}


@graph
def g():
    PARENT.declare(count=0, done=False)
    s = step(count=PARENT["count"])
    s["count"] >> PARENT["count"]
    s["done"] >> PARENT["done"]
    gen = emit_items(n=s["width"])
    d = double(item=gen["item"].parallel(max=4))
    START >> s >> if_(s["done"] == True, END).else_(gen)  # noqa: E712
    gen >> d >> s


@graph
def g_plain_backedge_still_terminates():
    PARENT.declare(count=0, done=False)
    s = step(count=PARENT["count"])
    s["count"] >> PARENT["count"]
    s["done"] >> PARENT["done"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712


@op
def step_wide_zero(count: int = 0) -> dict:
    count = (count or 0) + 1
    # `done` stays False so only the empty fan-out can stop this.
    return {"count": count, "done": False, "width": 0}


@graph
def g_generator_yielding_nothing_terminates():
    PARENT.declare(count=0, done=False)
    s = step_wide_zero(count=PARENT["count"])
    s["count"] >> PARENT["count"]
    s["done"] >> PARENT["done"]
    gen = emit_items(n=s["width"])
    d = double(item=gen["item"].parallel(max=4))
    # An exit is mandatory — the rewrite refuses a cycle without
    # one — but this branch never fires, so termination has to
    # come from the back-edge source never running.
    START >> s >> if_(s["done"] == True, END).else_(gen)  # noqa: E712
    gen >> d >> s


class TestLoopWithGeneratorInside:
    async def _run(self, build):
        built = build()
        result = await Operon(built).run(inputs={})
        return built, result

    @pytest.mark.asyncio
    async def test_collect_consumer_as_backedge_source_iterates(self):
        """The ReAct shape: fan out, collect, loop back."""
        seen = []

        @op
        def gather(values=None) -> dict:
            seen.append(values)
            return {"n": len(values or [])}

        @graph
        def g():
            PARENT.declare(count=0, done=False)
            s = step(count=PARENT["count"])
            s["count"] >> PARENT["count"]
            s["done"] >> PARENT["done"]
            gen = emit_items(n=s["width"])
            d = double(item=gen["item"].parallel(max=4))
            got = gather(values=d["doubled"].collect())
            START >> s >> if_(s["done"] == True, END).else_(gen)  # noqa: E712
            gen >> d >> got >> s

        built, result = await self._run(g)
        assert result["$state"][built.full_name, "count"] == 3, (
            "loop must iterate to its exit condition, not stop after one pass"
        )
        # `collect()` behind a per-item op waits for the whole stream: the
        # consumer runs once per dispatching iteration with both items.
        # (It used to run once per item with a one-element list.)
        assert seen == [[0, 2], [0, 2]]

    @pytest.mark.asyncio
    async def test_parallel_consumer_as_backedge_source_iterates(self):
        """Same defect one level shallower — no collect(), just fan-out."""

        built, result = await self._run(g)
        assert result["$state"][built.full_name, "count"] == 3

    @pytest.mark.asyncio
    async def test_plain_backedge_still_terminates(self):
        """The pre-existing path must be untouched — including the branch
        source case, where firing depends on which target was chosen."""

        built, result = await self._run(g_plain_backedge_still_terminates)
        assert result["$state"][built.full_name, "count"] == 3

    @pytest.mark.asyncio
    async def test_generator_yielding_nothing_terminates(self):
        """A fan-out over an empty list means the back-edge source never
        runs — the loop must stop rather than spin to max_iterations."""

        built, result = await self._run(g_generator_yielding_nothing_terminates)
        assert result["$state"][built.full_name, "count"] == 1
