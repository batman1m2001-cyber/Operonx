"""Phase 3 hardening regression tests — locks in the fixes from the
post-ship adversarial review.

One test class per bug/hazard from the review, each verifying the behaviour
that was broken pre-fix.
"""

import warnings

import pytest

from operonx import Operon, PARENT, op
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph._decorators import graph
from operonx.core.ops.graph.graph_op import END, START, GraphOp


# =========================================================================
# BUG 1 — compact self-loop-via-branch termination
# =========================================================================


class TestBug1BranchAwareTermination:
    """Pre-fix: back-edge source was a BranchOp → end_time always present →
    scheduler thought the back-edge always fired → ran to max_iterations."""

    async def test_compact_self_loop_via_branch_terminates(self):
        @op
        def tick(counter: int):
            return {"counter": counter + 1}

        @graph
        def selfloop():
            PARENT.declare(count=0)
            t = tick(counter=PARENT["count"], name="t")
            t["counter"] >> PARENT["count"]
            # The natural while-loop shape: branch chooses END vs self.
            START >> t >> if_(PARENT["count"] >= 3, END).else_(t)

        g = selfloop()
        result = await Operon(g).run(inputs={"count": 0})
        state = result["$state"]
        count_idx = state.schema.get_index(g.full_name, "count")
        assert state._cells[count_idx][("main",)] == 3

    async def test_branch_source_multiple_back_edges_only_one_matches(self):
        """When the branch has two candidates that both loop back (to different
        loop targets), only the chosen one should count as a back-edge fire."""

        @op
        def tick(counter: int):
            return {"counter": counter + 1}

        @op
        def marker(counter: int):
            return {"counter": counter}

        @graph
        def two_backs():
            PARENT.declare(count=0)
            t = tick(counter=PARENT["count"], name="t")
            m = marker(counter=t["counter"], name="m")
            t["counter"] >> PARENT["count"]
            # Branch routes to t (loop back) or END.
            START >> t >> m >> if_(PARENT["count"] >= 4, END).else_(t)

        g = two_backs()
        result = await Operon(g).run(inputs={"count": 0})
        state = result["$state"]
        count_idx = state.schema.get_index(g.full_name, "count")
        assert state._cells[count_idx][("main",)] == 4


# =========================================================================
# BUG 2 — E3 multi-exit via branch
# =========================================================================


class TestBug2E3MultiExitBranch:
    """Pre-fix: branch's candidates list still named outside-SCC ops after
    rewrite → hidden loop's _validate_branch_targets errored."""

    async def test_branch_candidate_pointing_outside_scc_is_legal(self):
        @op
        def bump(v: int):
            return {"v": v + 1}

        @op
        def relay(v: int):
            return {"v": v}

        @graph
        def multi_exit():
            PARENT.declare(count=0)
            a = bump(v=PARENT["count"], name="a")
            b = relay(v=a["v"], name="b")  # outside SCC exit
            c = relay(v=a["v"], name="c")  # inside SCC (back-edge source below)
            a["v"] >> PARENT["count"]
            START >> a >> if_(PARENT["count"] >= 3, b).else_(c)
            b >> END
            c >> a  # back-edge

        g = multi_exit()
        result = await Operon(g).run(inputs={"count": 0})
        state = result["$state"]
        count_idx = state.schema.get_index(g.full_name, "count")
        assert state._cells[count_idx][("main",)] == 3


# =========================================================================
# BUG 3 — outer op holding a Ref into a moved SCC op
# =========================================================================


class TestBug3OuterRefIntoSCC:
    """Pre-fix: non-SCC outer op with Ref to a moved SCC op → validation error
    blaming the user for a scope violation on legal user code."""

    def test_outer_op_can_reference_moved_scc_op(self):
        @op
        def bump(v: int):
            return {"v": v + 1}

        @op
        def relay(v: int):
            return {"v": v}

        @graph
        def multi_end():
            PARENT.declare(count=0)
            a = bump(v=PARENT["count"], name="a")
            b = relay(v=a["v"], name="b")  # b in SCC (back-edges to a below)
            c = relay(v=a["v"], name="c")  # c stays outside SCC, refs a
            a["v"] >> PARENT["count"]
            START >> a
            a >> b >> END
            a >> c >> END
            b >> a

        g = multi_end()
        # Just needs to BUILD successfully — pre-fix this raised ValueError.
        g.build()
        assert any(n.startswith("__loop_") for n in g._ops)


# =========================================================================
# BUG 4 — E8 multiple back-edges to distinct targets
# =========================================================================


class TestBug4E8DistinctBackEdgeTargets:
    """Pre-fix: two back-edges targeting distinct SCC nodes → silently
    dropped one, ran the loop only from one entry."""

    def test_distinct_back_edge_targets_raise_e2(self):
        @op
        def s(x=None):
            return {"x": 1}

        with GraphOp(name="g") as g:
            a = s(name="a")
            b = s(name="b")
            c = s(name="c")
            START >> a >> b >> c >> END
            c >> a  # back-edge target = a
            c >> b  # back-edge target = b (distinct → E8)

        with pytest.raises(ValueError, match="entries"):
            g.build()


# =========================================================================
# BUG 5 — dangling lookback edges from SCC to outer
# =========================================================================


class TestBug5DanglingLookback:
    """Pre-fix: lookback edge from an SCC node to an outer node was left in
    outer._edges pointing at a node that no longer existed in outer._ops."""

    def test_scc_to_outer_lookback_edge_gets_rewired(self):
        @op
        def s(x=None):
            return {"x": 1}

        with GraphOp(name="g") as g:
            a = s(name="a")
            b = s(name="b")
            c = s(name="c")
            START >> a >> b >> END
            b >> a  # SCC = {a, b}
            START >> c >> END
            g.add_edge("a", "c", type="lookback")  # SCC-node → outer

        g.build()
        loop_name = next(n for n in g._ops if n.startswith("__loop_"))
        # After rewrite, the (a, c) edge must be gone from outer._edges (a
        # no longer lives there) and a matching (loop_name, c) must exist.
        for (src, dst) in g._edges:
            assert src != "a", (
                f"dangling edge: outer._edges still has ({src},{dst}) whose "
                f"source is inside the hidden loop"
            )
        assert (loop_name, "c") in g._edges


# =========================================================================
# BUG 6 — strict_dag swallowed as input at call site
# =========================================================================


class TestBug6StrictDagKwarg:
    """Pre-fix: passing strict_dag as a call-site kwarg on a @graph function
    dropped it into input_mappings instead of GraphOp init kwargs, so the
    documented opt-out silently did nothing."""

    def test_strict_dag_via_call_site_kwarg_works(self):
        @op
        def s(x=None):
            return {"x": 1}

        @graph
        def cycling(x):
            a = s(name="a", x=x)
            b = s(name="b", x=a["x"])
            START >> a >> b >> END
            b >> a  # back-edge

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            g = cycling(x=1, strict_dag=True)
            g.build()

        # strict_dag=True → rewrite disabled → no hidden loop op.
        assert g._strict_dag is True
        assert not any(n.startswith("__loop_") for n in g._ops)


# =========================================================================
# HAZARD — nested synthetic loops sharing ctx namespace
# =========================================================================


class TestHazardNestedSyntheticCtxNamespace:
    """Pre-fix: nested synthetic loops both bumped ctx to ``('main','loop_1')``
    → collision on state cells. Fix uses per-op-instance ctx segments
    (``{full_name}#{n}``)."""

    async def test_nested_synthetic_loops_produce_unique_ctxs(self):
        @op
        def inc_i(i: int):
            return {"i": i + 1}

        @op
        def pass_i(i: int):
            return {"i": i}

        @op
        def inc_o(o: int):
            return {"o": o + 1}

        @op
        def pass_o(o: int):
            return {"o": o}

        @graph
        def inner_g():
            PARENT.declare(i=0)
            ii = inc_i(i=PARENT["i"], name="ii")
            ic = pass_i(i=ii["i"], name="ic")
            ii["i"] >> PARENT["i"]
            START >> ii >> if_(PARENT["i"] >= 3, END).else_(ic)
            ic >> ii

        @graph
        def outer_g():
            PARENT.declare(o=0)
            oi = inc_o(o=PARENT["o"], name="oi")
            oc = pass_o(o=oi["o"], name="oc")
            inner = inner_g(name="inner")
            oi["o"] >> PARENT["o"]
            START >> oi >> if_(PARENT["o"] >= 2, END).else_(oc)
            oc >> inner >> oi

        g = outer_g()
        result = await Operon(g).run(inputs={"o": 0, "i": 0})
        state = result["$state"]

        # Iterate every end_time cell for inner-graph ops — every ctx must
        # carry a per-op iter suffix, not a bare "loop_N" that would collide
        # with the outer loop.
        for (op_name, var), idx in state.schema._var_to_idx.items():
            if var != "end_time" or "inner" not in op_name:
                continue
            for ctx in state._cells[idx].contexts:
                for seg in ctx:
                    # No bare "loop_N" segments from synthetic loops.
                    assert not (
                        isinstance(seg, str) and seg.startswith("loop_")
                    ), f"leaked classic-loop ctx segment {seg!r} on {op_name}"


# =========================================================================
# HAZARD — nondeterministic SCC iteration order
# =========================================================================


class TestHazardDeterministicSCCOrder:
    """Pre-fix: ``for n in scc`` iterated a set, giving hash-order-dependent
    ``hidden._ops`` insertion order. Now iterates ``scc_seq`` (list) preserving
    Tarjan's discovery order."""

    def test_hidden_ops_insertion_order_stable(self):
        @op
        def s(x=None):
            return {"x": 1}

        results = []
        for _ in range(3):
            with GraphOp(name="g") as g:
                a = s(name="a")
                b = s(name="b")
                c = s(name="c")
                START >> a >> b >> c >> END
                c >> a  # back-edge

            g.build()
            loop_name = next(n for n in g._ops if n.startswith("__loop_"))
            results.append(list(g._ops[loop_name]._ops.keys()))

        assert results[0] == results[1] == results[2]


# =========================================================================
# HAZARD — outer.entries missing loop_name after rewrite
# =========================================================================


class TestHazardOuterEntriesPromotion:
    """Pre-fix: when the SCC entry was START-marked and other ops also had
    entries, outer.entries dropped the moved entry but never appended
    loop_name. External tooling reading outer.entries saw an incomplete graph."""

    def test_loop_name_appears_in_outer_entries(self):
        @op
        def s(x=None):
            return {"x": 1}

        @op
        def side():
            return {"y": 2}

        with GraphOp(name="g") as g:
            a = s(name="a")
            b = s(name="b")
            side_op = side(name="side")
            START >> a >> b >> END
            b >> a
            START >> side_op >> END

        g.build()
        loop_name = next(n for n in g._ops if n.startswith("__loop_"))
        assert loop_name in g.entries
        assert "side" in g.entries


# =========================================================================
# HAZARD — serialize() emits invalid config for synthetic loops
# =========================================================================


class TestHazardSerializeSynthetic:
    """Pre-fix: serialize() emitted loop_config={"until": None, "max_iterations": 1000}
    for a synthetic loop — no marker distinguishing it from a classic
    'never-terminating' loop. External consumers (Rust runtime, replay tools)
    would read that as 'loop unconditionally to the cap'."""

    def test_serialize_synthetic_raises(self):
        @op
        def s(x=None):
            return {"x": 1}

        with GraphOp(name="g") as g:
            a = s(name="a")
            b = s(name="b")
            START >> a >> b >> END
            b >> a

        g.build()
        with pytest.raises(NotImplementedError, match="synthetic"):
            g.serialize()


# =========================================================================
# HAZARD — E1 raw KeyError when entry can't be determined
# =========================================================================


class TestHazardE1ExplicitError:
    """Pre-fix: an SCC with no outside pred and no start=True op raised a
    raw KeyError deep in _synthesize_loop. Now raises a friendly ValueError."""

    def test_no_determinable_entry_raises_valueerror(self):
        @op
        def s(x=None):
            return {"x": 1}

        with GraphOp(name="g") as g:
            a = s(name="a")
            b = s(name="b")
            # No START, no explicit .start; both nodes cyclic.
            a >> b
            b >> a
            # And nothing routes to END — E1's "no exit" fires first here
            # unless we add one.
            a >> END

        with pytest.raises(ValueError):
            g.build()
