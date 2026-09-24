"""`enabled=False` on a subgraph must stop the ops inside it.

`BaseOp.run` has always returned early when an op is disabled, but
`GraphOp` overrides `run` and did not repeat the check. So switching a
subgraph off silenced its *output* while every op inside it still
executed — including LLM calls.

Found by a replay harness that poisons network ops: a case switched off
for the run still reached its model, which is a consumer paying for a
stage it believed was off and having no way to notice. The verdict was
discarded downstream, so the only visible symptom was the bill.
"""

from __future__ import annotations

import pytest

from operonx.core import END, START, GraphOp, Operon, graph
from operonx.core.ops.transform.func_op import op


@op
def seed(n: int = 0) -> dict:
    return {"n": n}


def _graph_with_child(ran: list):
    """A parent with one nested subgraph. Returns `(parent, child_node)`.

    The child is composed *inside* the parent's context, which is how a
    subgraph becomes one of the parent's ops — building it in its own
    `with` block leaves the parent with an edge to something it does not
    contain.
    """

    @op
    def inner(n: int = 0) -> dict:
        ran.append("inner")
        return {"doubled": n * 2}

    @graph
    def child(n: int = 0):
        i = inner(n=n)
        START >> i >> END

    with GraphOp(name="parent") as parent:
        s = seed(n=1)
        blk = child(n=s["n"], name="child")
        START >> s >> blk >> END

    return parent, parent._ops["child"]


class TestDisabledSubgraph:
    def test_its_ops_do_not_run(self):
        """The bug: the subgraph reported nothing and ran everything."""
        ran = []
        parent, child = _graph_with_child(ran)
        child.enabled = False
        import asyncio

        asyncio.run(Operon(parent).run(inputs={"n": 1}))
        assert ran == [], "a disabled subgraph must not execute its children"

    def test_enabled_by_default(self):
        ran = []
        parent, _child = _graph_with_child(ran)
        import asyncio

        asyncio.run(Operon(parent).run(inputs={"n": 1}))
        assert ran == ["inner"]

    def test_re_enabling_works(self):
        """The flag is flipped per run by consumers; it must not latch."""
        ran = []
        parent, child = _graph_with_child(ran)
        child.enabled = False
        import asyncio

        asyncio.run(Operon(parent).run(inputs={"n": 1}))
        child.enabled = True
        asyncio.run(Operon(parent).run(inputs={"n": 1}))
        assert ran == ["inner"]

    def test_the_rest_of_the_graph_still_runs(self):
        """Disabling one stage must not take the whole run down with it."""
        ran = []
        parent, child = _graph_with_child(ran)
        child.enabled = False
        import asyncio

        out = asyncio.run(Operon(parent).run(inputs={"n": 3}))
        assert out is not None
        assert ran == []


class TestDisabledPlainOp:
    """The behaviour `GraphOp` was missing, asserted so it cannot drift."""

    def test_a_disabled_func_op_does_not_run(self):
        ran = []

        @op
        def worker(n: int = 0) -> dict:
            ran.append("worker")
            return {"out": n}

        with GraphOp(name="g") as g:
            s = seed(n=1)
            w = worker(n=s["n"])
            START >> s >> w >> END
        w.enabled = False

        import asyncio

        asyncio.run(Operon(g).run(inputs={"n": 1}))
        assert ran == []


class TestDisabledDoesNotStallTheGraph:
    """ "Completed, produced nothing" — not "never completed".

    The first attempt at the fix returned without yielding, which ends the
    generator and leaves every successor waiting forever. Disabling one
    stage then silently stalled everything downstream: in the pipeline this
    surfaced as a scored batch producing no rows at all.

    That failure mode already existed in `BaseOp.run` for plain ops and
    nobody had hit it, because the only consumer disabling anything
    disabled *subgraphs* — which ran regardless, thanks to the bug above.
    Two defects cancelling out.
    """

    def _chain(self, ran: list):
        @op
        def inner() -> dict:
            ran.append("inner")
            return {"a": 1}

        @graph
        def child():
            i = inner()
            START >> i >> END

        @op
        def after(a=None) -> dict:
            ran.append("after")
            return {"out": a}

        with GraphOp(name="g") as g:
            c = child(name="child")
            s = after(a=c["a"])
            START >> c >> s >> END
        return g, g._ops["child"]

    def test_the_successor_still_runs(self):
        import asyncio

        ran = []
        g, child = self._chain(ran)
        child.enabled = False
        out = asyncio.run(Operon(g).run(inputs={}))
        assert "after" in ran, "a disabled op must not block what comes next"
        assert "inner" not in ran, "...while still not running its own children"
        assert "out" in out

    def test_the_successor_reads_none_for_the_skipped_fields(self):
        """Absent, not stale: nothing was written, so nothing is read."""
        import asyncio

        ran = []
        g, child = self._chain(ran)
        child.enabled = False
        out = asyncio.run(Operon(g).run(inputs={}))
        assert out.get("out") is None
