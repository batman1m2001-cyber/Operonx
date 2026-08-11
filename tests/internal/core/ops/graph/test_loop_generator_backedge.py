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
"""

from __future__ import annotations

import pytest

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.task_scheduler import _ctx_within, _ctxs_within

pytestmark = pytest.mark.unit


class _FakeCell:
    """Minimal stand-in for Cell — only what `_ctxs_within` touches."""

    def __init__(self, contexts):
        self.contexts = {c: object() for c in contexts}

    def __contains__(self, ctx):
        return ctx in self.contexts


class TestCtxWithin:
    def test_equal_context_is_within(self):
        assert _ctx_within(("main",), ("main",)) is True

    def test_deeper_context_is_within(self):
        assert _ctx_within(("main", "[0]", "__collect__"), ("main",)) is True

    def test_sibling_iteration_is_not_within(self):
        """The property the whole fix rests on: iteration ctxs are
        siblings, so a stale iteration cannot satisfy the next one."""
        assert _ctx_within(("main", "[0]"), ("main", "g.__loop_0__#1")) is False

    def test_shallower_context_is_not_within(self):
        assert _ctx_within(("main",), ("main", "g.__loop_0__#1")) is False

    def test_prefix_match_is_by_segment_not_string(self):
        assert _ctx_within(("mainline",), ("main",)) is False


class TestCtxsWithin:
    def test_exact_match_short_circuits(self):
        """The pre-existing fast path must stay free: an exact hit returns
        without scanning, so loops that work today pay nothing."""
        cell = _FakeCell([("main",), ("main", "[0]"), ("main", "[1]")])
        assert _ctxs_within(cell, ("main",)) == [("main",)]

    def test_finds_descendants_when_no_exact_match(self):
        cell = _FakeCell([("main", "[0]", "__collect__")])
        assert _ctxs_within(cell, ("main",)) == [("main", "[0]", "__collect__")]

    def test_newest_first(self):
        """Reverse order keeps the scan O(1) in practice — the current
        iteration's contexts were inserted last."""
        cell = _FakeCell([("main", "a", "[0]"), ("main", "a", "[1]")])
        assert _ctxs_within(cell, ("main", "a")) == [
            ("main", "a", "[1]"),
            ("main", "a", "[0]"),
        ]

    def test_ignores_stale_iterations(self):
        cell = _FakeCell([("main", "[0]"), ("main", "g.__loop_0__#1", "[0]")])
        assert _ctxs_within(cell, ("main", "g.__loop_0__#1")) == [("main", "g.__loop_0__#1", "[0]")]

    def test_empty_when_op_did_not_run(self):
        assert _ctxs_within(_FakeCell([]), ("main",)) == []


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
        # Measured, not assumed: inside a loop, `collect()` behind
        # `parallel()` invokes the consumer once per item with a
        # single-element list rather than once with the whole batch —
        # 2 items x 2 dispatching iterations. Consumers must therefore
        # tolerate a partial batch; operonx.agents.graphs.react's
        # `gather_tool_messages` does, and its results still merge
        # correctly because the reducer accumulates per write.
        assert seen == [[0], [2], [0], [2]]

    @pytest.mark.asyncio
    async def test_parallel_consumer_as_backedge_source_iterates(self):
        """Same defect one level shallower — no collect(), just fan-out."""

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

        built, result = await self._run(g)
        assert result["$state"][built.full_name, "count"] == 3

    @pytest.mark.asyncio
    async def test_plain_backedge_still_terminates(self):
        """The pre-existing path must be untouched — including the branch
        source case, where firing depends on which target was chosen."""

        @graph
        def g():
            PARENT.declare(count=0, done=False)
            s = step(count=PARENT["count"])
            s["count"] >> PARENT["count"]
            s["done"] >> PARENT["done"]
            START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712

        built, result = await self._run(g)
        assert result["$state"][built.full_name, "count"] == 3

    @pytest.mark.asyncio
    async def test_generator_yielding_nothing_terminates(self):
        """A fan-out over an empty list means the back-edge source never
        runs — the loop must stop rather than spin to max_iterations."""

        @op
        def step_wide_zero(count: int = 0) -> dict:
            count = (count or 0) + 1
            # `done` stays False so only the empty fan-out can stop this.
            return {"count": count, "done": False, "width": 0}

        @graph
        def g():
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

        built, result = await self._run(g)
        assert result["$state"][built.full_name, "count"] == 1
