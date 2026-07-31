"""Tests for the auto-soften-branch-merge pass in GraphOp.build().

The pass runs during ``build()`` and flips ``edge.soft = True`` for edges into
a merge op whose predecessors trace back to a common ``BranchOp`` ancestor via
disjoint first-hop children. This mirrors the manual ``~merge_op`` idiom users
had to write on every branch-fan-in site.
"""

from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp


def _mk(name, out_key="x", **inputs):
    """Tiny factory: a FuncOp that echoes its inputs into a single output."""
    if not inputs:
        return FuncOp(name=name, code_fn=lambda: {out_key: 1}, inputs={})
    # take the first input arg and pass it through
    (input_key, input_ref) = next(iter(inputs.items()))
    return FuncOp(
        name=name,
        code_fn=lambda **kw: {out_key: kw.get(input_key, 1)},
        inputs=inputs,
    )


def _edge(graph, src, dst):
    return next(
        (e for e in graph._edges.values() if e.from_node == src and e.to_node == dst),
        None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shape 1 — 2-way branch, both tails to merge → both auto-softened
# ─────────────────────────────────────────────────────────────────────────────
def test_shape1_two_way_branch_both_auto_softened():
    with GraphOp(name="s1") as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 5, "a").else_("b")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a >> m
        router >> b >> m
        m >> END

    g.build()

    assert _edge(g, "a", "m").soft is True, "a→m should be auto-softened"
    assert _edge(g, "a", "m").auto_soft is True
    assert _edge(g, "b", "m").soft is True, "b→m should be auto-softened"
    assert _edge(g, "b", "m").auto_soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Shape 2 — mixed: 2 branch predecessors + 1 external hard predecessor
#   The branch preds get softened; the external hard pred stays hard.
# ─────────────────────────────────────────────────────────────────────────────
def test_shape2_branch_preds_softened_external_stays_hard():
    with GraphOp(name="s2") as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 5, "a").else_("b")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        c = _mk("c", out_key="y")  # independent — always fires
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a >> m
        router >> b >> m
        START >> c >> m
        m >> END

    g.build()

    assert _edge(g, "a", "m").soft is True
    assert _edge(g, "b", "m").soft is True
    assert _edge(g, "c", "m").soft is False, "external c→m must stay hard"
    assert _edge(g, "c", "m").auto_soft is False


# ─────────────────────────────────────────────────────────────────────────────
# Shape 3 — one edge already manually soft, sibling auto-softened.
#   No double-flip; already-soft edges are left alone.
# ─────────────────────────────────────────────────────────────────────────────
def test_shape3_manual_soft_and_auto_soft_coexist():
    with GraphOp(name="s3") as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 5, "a").else_("b")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a >> ~m  # manual soft
        router >> b >> m  # will be auto-softened
        m >> END

    g.build()

    e_am = _edge(g, "a", "m")
    e_bm = _edge(g, "b", "m")
    assert e_am.soft is True
    assert e_am.auto_soft is False, "manual ~ should not be flagged as auto"
    assert e_bm.soft is True
    assert e_bm.auto_soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Shape 4 — 3-way branch, all three tails to merge → all softened
# ─────────────────────────────────────────────────────────────────────────────
def test_shape4_three_way_branch_all_softened():
    with GraphOp(name="s4") as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 90, "a").if_(seed["score"] >= 70, "b").else_("c")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        c = _mk("c", out_key="y", score=router["target"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a >> m
        router >> b >> m
        router >> c >> m
        m >> END

    g.build()

    assert _edge(g, "a", "m").soft is True
    assert _edge(g, "b", "m").soft is True
    assert _edge(g, "c", "m").soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Shape 5 — diamond WITHOUT a branch: two preds share a non-branch ancestor.
#   The two preds always both fire → must NOT be auto-softened.
# ─────────────────────────────────────────────────────────────────────────────
def test_shape5_diamond_no_branch_no_softening():
    with GraphOp(name="s5") as g:
        seed = _mk("seed", out_key="x")  # NOT a branch — plain func op
        a = _mk("a", out_key="y", x=seed["x"])
        b = _mk("b", out_key="y", x=seed["x"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed
        seed >> a >> m
        seed >> b >> m
        m >> END

    g.build()

    assert _edge(g, "a", "m").soft is False, "no branch ancestor → no softening"
    assert _edge(g, "b", "m").soft is False


# ─────────────────────────────────────────────────────────────────────────────
# Shape 6 — per-graph opt-out via auto_soft=False
# ─────────────────────────────────────────────────────────────────────────────
def test_shape6_per_graph_opt_out():
    with GraphOp(name="s6", auto_soft=False) as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 5, "a").else_("b")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a >> ~m  # user still uses manual ~ when opting out
        router >> b >> ~m
        m >> END

    g.build()

    assert _edge(g, "a", "m").auto_soft is False, "auto_soft=False disables the pass"
    assert _edge(g, "b", "m").auto_soft is False
    # manual ~ still works
    assert _edge(g, "a", "m").soft is True
    assert _edge(g, "b", "m").soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Shape 7 — nested subgraph: each graph runs its own pass independently
# ─────────────────────────────────────────────────────────────────────────────
def test_shape7_nested_subgraph():
    # Inner graph with its own branch/merge pattern
    with GraphOp(name="inner") as inner:
        i_seed = _mk("i_seed", out_key="score")
        i_router = if_(i_seed["score"] >= 5, "ia").else_("ib")
        ia = _mk("ia", out_key="y", score=i_router["target"])
        ib = _mk("ib", out_key="y", score=i_router["target"])
        im = _mk("im", out_key="z", y=ia["y"])
        START >> i_seed >> i_router
        i_router >> ia >> im
        i_router >> ib >> im
        im >> END

    inner.build()

    assert _edge(inner, "ia", "im").soft is True
    assert _edge(inner, "ib", "im").soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Shape 8 — per-edge opt-out via add_edge(..., hard=True)
# ─────────────────────────────────────────────────────────────────────────────
def test_shape8_per_edge_hard_opt_out():
    with GraphOp(name="s8") as g:
        seed = _mk("seed", out_key="score")
        router = if_(seed["score"] >= 5, "a").else_("b")
        a = _mk("a", out_key="y", score=router["target"])
        b = _mk("b", out_key="y", score=router["target"])
        m = _mk("m", out_key="z", y=a["y"])
        START >> seed >> router
        router >> a
        router >> b
        # b→m stays hard by user's opt-out; a→m still auto-softens
        g.add_edge("a", "m")
        g.add_edge("b", "m", hard=True)
        m >> END

    g.build()

    assert _edge(g, "b", "m").soft is False, "hard=True pinned"
    assert _edge(g, "b", "m").pinned_hard is True
    # a→m is the ONLY hard candidate now; no sibling to soften against
    # (b is pinned_hard so its sig is still counted, but the auto-pass skips
    # it as a candidate for flipping — a still finds b as its exclusive sibling)
    assert _edge(g, "a", "m").soft is True
    assert _edge(g, "a", "m").auto_soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Bonus — nested branches: deep ancestry
# ─────────────────────────────────────────────────────────────────────────────
def test_nested_branches():
    with GraphOp(name="nested") as g:
        seed = _mk("seed", out_key="score")
        outer = if_(seed["score"] >= 50, "middle").else_("z")
        middle = _mk("middle", out_key="score2", score=outer["target"])
        inner = if_(middle["score2"] >= 50, "x").else_("y")
        x = _mk("x", out_key="v", score=inner["target"])
        y = _mk("y", out_key="v", score=inner["target"])
        z = _mk("z", out_key="v", score=outer["target"])
        m = _mk("m", out_key="w", v=x["v"])
        START >> seed >> outer
        outer >> middle >> inner
        inner >> x >> m
        inner >> y >> m
        outer >> z >> m
        m >> END

    g.build()

    # x,y,z all trace to `outer` via disjoint first-hop children
    # (x/y → middle; z → z). All three should soften.
    assert _edge(g, "x", "m").soft is True
    assert _edge(g, "y", "m").soft is True
    assert _edge(g, "z", "m").soft is True
