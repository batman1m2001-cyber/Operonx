"""Tests for the inline ``if_(...).else_(...)`` API.

The inline form:
- accepts op instances (not just string names) as case/default targets
- auto-wires ``branch >> target`` for every op-instance target
- resolves the branch's own name via auto_name (LHS) then falls back to
  ``route_N`` per-graph counter

Backward compat: string targets skip auto-wiring — user still writes
``branch >> target`` manually for forward-referenced targets.
"""

from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp


def _seed(name="seed", out_key="score"):
    return FuncOp(name=name, code_fn=lambda: {out_key: 1}, inputs={})


def _passthrough(name, in_key="score", out_key="y", **kwargs):
    return FuncOp(
        name=name,
        code_fn=lambda **kw: {out_key: kw.get(in_key, 1)},
        inputs=kwargs,
    )


def _edges_from(graph, src):
    return sorted(
        e.to_node for e in graph._edges.values() if e.from_node == src
    )


# ─────────────────────────────────────────────────────────────────────────────
# Inline form — op-instance targets → auto-wire edges + auto-name
# ─────────────────────────────────────────────────────────────────────────────
def test_inline_op_instance_targets_auto_wire():
    """``source >> if_(cond, a).else_(b)`` auto-adds branch→a and branch→b."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> if_(seed["score"] >= 5, a).else_(b)
        a >> m
        b >> m
        m >> END

    g.build()

    branch_name = next(op.name for op in g._ops.values() if op.type == "branch")
    outgoing = _edges_from(g, branch_name)
    assert outgoing == ["a", "b"], f"branch should auto-wire to a and b, got {outgoing}"


def test_inline_lhs_assignment_uses_variable_name():
    """``route = if_(cond, a).else_(b)`` gets ``route`` as its name."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        route = if_(seed["score"] >= 5, a).else_(b)
        START >> seed >> route
        a >> m
        b >> m
        m >> END

    g.build()

    assert route.name == "route"
    assert "route" in g._ops


def test_inline_no_lhs_falls_back_to_route_counter():
    """Inline with no LHS → ``route_1`` (per-graph counter)."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> if_(seed["score"] >= 5, a).else_(b)
        a >> m
        b >> m
        m >> END

    g.build()

    branch_names = [op.name for op in g._ops.values() if op.type == "branch"]
    assert branch_names == ["route_1"], f"expected ['route_1'], got {branch_names}"


def test_inline_multiple_branches_get_route_1_route_2():
    """Multiple inline branches in one graph get sequential counters."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        c = _passthrough("c", in_key="score", score=seed["score"])
        d = _passthrough("d", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> if_(seed["score"] >= 5, a).else_(b)
        # Second branch, still inline, no LHS
        a >> if_(seed["score"] >= 10, c).else_(d)
        b >> m
        c >> m
        d >> m
        m >> END

    g.build()

    branch_names = sorted(op.name for op in g._ops.values() if op.type == "branch")
    assert branch_names == ["route_1", "route_2"], f"got {branch_names}"


# ─────────────────────────────────────────────────────────────────────────────
# Backward compat — string targets still work exactly as before
# ─────────────────────────────────────────────────────────────────────────────
def test_string_targets_no_auto_wire():
    """String-name targets are forward refs — no auto-wiring, user wires manually."""
    with GraphOp(name="g") as g:
        seed = _seed()
        route = if_(seed["score"] >= 5, "a").else_("b")
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> route
        route >> a >> m  # user wires manually
        route >> b >> m
        m >> END

    g.build()

    outgoing = _edges_from(g, route.name)
    assert outgoing == ["a", "b"], f"user-wired edges only, got {outgoing}"


def test_mixed_op_instance_and_string_targets():
    """Mixed: op-instance case auto-wires, string case does not."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        # b is a forward ref — string
        m = _passthrough("m", in_key="y", y=a["y"])

        route = if_(seed["score"] >= 5, a).else_("b")
        b = _passthrough("b", in_key="score", score=seed["score"])

        START >> seed >> route
        route >> b >> m
        a >> m
        m >> END

    g.build()

    outgoing = _edges_from(g, route.name)
    assert outgoing == ["a", "b"], f"a auto-wired, b user-wired, got {outgoing}"


# ─────────────────────────────────────────────────────────────────────────────
# Interaction with auto-soft-edge (regression: auto-soften still fires)
# ─────────────────────────────────────────────────────────────────────────────
def test_inline_form_auto_softens_merge_edges():
    """Inline branch + auto-wire + auto-soft = zero ``~`` needed."""
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> if_(seed["score"] >= 5, a).else_(b)
        a >> m
        b >> m
        m >> END

    g.build()

    # a→m and b→m should be auto-softened by the branch-merge pass
    e_am = next(e for e in g._edges.values() if e.from_node == "a" and e.to_node == "m")
    e_bm = next(e for e in g._edges.values() if e.from_node == "b" and e.to_node == "m")
    assert e_am.soft is True and e_am.auto_soft is True
    assert e_bm.soft is True and e_bm.auto_soft is True


# ─────────────────────────────────────────────────────────────────────────────
# Three-way inline branch
# ─────────────────────────────────────────────────────────────────────────────
def test_inline_three_way_branch():
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        c = _passthrough("c", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        START >> seed >> (
            if_(seed["score"] >= 90, a).if_(seed["score"] >= 70, b).else_(c)
        )
        a >> m
        b >> m
        c >> m
        m >> END

    g.build()

    branch_name = next(op.name for op in g._ops.values() if op.type == "branch")
    outgoing = _edges_from(g, branch_name)
    assert outgoing == ["a", "b", "c"]


# ─────────────────────────────────────────────────────────────────────────────
# .build() (no else) also auto-wires
# ─────────────────────────────────────────────────────────────────────────────
def test_inline_build_no_default_auto_wires():
    with GraphOp(name="g") as g:
        seed = _seed()
        a = _passthrough("a", in_key="score", score=seed["score"])
        b = _passthrough("b", in_key="score", score=seed["score"])
        m = _passthrough("m", in_key="y", y=a["y"])

        route = if_(seed["score"] >= 90, a).if_(seed["score"] >= 50, b).build()
        START >> seed >> route
        a >> m
        b >> m
        m >> END

    g.build()

    outgoing = _edges_from(g, route.name)
    assert outgoing == ["a", "b"]
