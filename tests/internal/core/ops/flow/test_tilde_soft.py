"""Test using ~ (tilde) for soft edges: a >> ~b means soft edge to b

Uses the actual implementation from operonx.core.ops.base
"""

import pytest

from operonx.core.ops.base import END, START
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp


class TestTildeSoftEdge:
    """Test ~ syntax for soft edges with real nodes."""

    def test_basic_soft_edge(self):
        """Test: a >> ~b creates soft edge"""
        with GraphOp(name="test") as graph:
            a = FuncOp(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = FuncOp(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})

            START >> a >> ~b >> END

        graph.build()

        # Check edges
        a_to_b = next(
            (e for e in graph._edges.values() if e.from_node == "a" and e.to_node == "b"), None
        )
        assert a_to_b is not None, "Edge a->b should exist"
        assert a_to_b.soft == True, "Edge a->b should be soft"

    def test_basic_hard_edge(self):
        """Test: a >> b creates hard edge"""
        with GraphOp(name="test") as graph:
            a = FuncOp(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = FuncOp(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})

            START >> a >> b >> END

        graph.build()

        # Check edges
        a_to_b = next(
            (e for e in graph._edges.values() if e.from_node == "a" and e.to_node == "b"), None
        )
        assert a_to_b is not None, "Edge a->b should exist"
        assert a_to_b.soft == False, "Edge a->b should be hard"

    def test_mixed_chain(self):
        """Test: a >> ~b >> c >> ~d >> e creates mixed edges"""
        with GraphOp(name="test") as graph:
            a = FuncOp(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = FuncOp(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})
            c = FuncOp(name="c", code_fn=lambda y: {"z": y}, inputs={"y": b["y"]})
            d = FuncOp(name="d", code_fn=lambda z: {"w": z}, inputs={"z": c["z"]})
            e = FuncOp(name="e", code_fn=lambda w: {"v": w}, inputs={"w": d["w"]})

            START >> a >> ~b >> c >> ~d >> e >> END

        graph.build()

        # Check each edge
        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges.values()}

        assert edges.get(("a", "b")) == True, "a->b should be soft"
        assert edges.get(("b", "c")) == False, "b->c should be hard"
        assert edges.get(("c", "d")) == True, "c->d should be soft"
        assert edges.get(("d", "e")) == False, "d->e should be hard"

    def test_branch_pattern(self):
        """Test branch pattern: branch >> ~case1 >> merge"""
        with GraphOp(name="test") as graph:
            branch = FuncOp(name="branch", code_fn=lambda: {"x": 1}, inputs={})
            case1 = FuncOp(name="case1", code_fn=lambda x: {"y": x}, inputs={"x": branch["x"]})
            case2 = FuncOp(name="case2", code_fn=lambda x: {"y": x}, inputs={"x": branch["x"]})
            merge = FuncOp(name="merge", code_fn=lambda y: {"z": y}, inputs={"y": case1["y"]})

            START >> branch
            branch >> ~case1 >> merge
            branch >> ~case2 >> merge
            merge >> END

        graph.build()

        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges.values()}

        assert edges.get(("branch", "case1")) == True, "branch->case1 should be soft"
        assert edges.get(("branch", "case2")) == True, "branch->case2 should be soft"
        assert edges.get(("case1", "merge")) == False, "case1->merge should be hard"
        assert edges.get(("case2", "merge")) == False, "case2->merge should be hard"

    def test_start_soft_edge_raises_error(self):
        """Test that START >> ~node raises error"""
        with pytest.raises(TypeError, match="Cannot use soft edge"):
            with GraphOp(name="test") as graph:
                a = FuncOp(name="a", code_fn=lambda: {"x": 1}, inputs={})
                START >> ~a  # Should raise

    def test_all_soft_chain(self):
        """Test: a >> ~b >> ~c >> ~d creates all soft edges"""
        with GraphOp(name="test") as graph:
            a = FuncOp(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = FuncOp(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})
            c = FuncOp(name="c", code_fn=lambda y: {"z": y}, inputs={"y": b["y"]})
            d = FuncOp(name="d", code_fn=lambda z: {"w": z}, inputs={"z": c["z"]})

            START >> a >> ~b >> ~c >> ~d >> END

        graph.build()

        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges.values()}

        assert edges.get(("a", "b")) == True, "a->b should be soft"
        assert edges.get(("b", "c")) == True, "b->c should be soft"
        assert edges.get(("c", "d")) == True, "c->d should be soft"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestTildeOnASentinel:
    """`~END` reads like it means something and never did.

    `~` is a statement about ready-counting: it makes the target fire when
    any one soft predecessor completes. A sentinel has no ready-count.
    Edges into `END` are unconditionally soft already, and `END` is output
    forwarding at build time rather than a node the scheduler waits on —
    so `~END` was a no-op twice over, and it appeared in three shipped
    examples because it looked meaningful.
    """

    @pytest.mark.parametrize("sentinel", ["END", "START", "PARENT"])
    def test_it_raises_rather_than_doing_nothing(self, sentinel):
        from operonx.core import END, PARENT, START

        marker = {"END": END, "START": START, "PARENT": PARENT}[sentinel]
        with pytest.raises(TypeError, match="sentinel"):
            ~marker

    def test_the_message_says_what_to_write_instead(self):
        from operonx.core import END

        with pytest.raises(TypeError) as exc:
            ~END
        assert ">> __END__" in str(exc.value)
        assert "already soft" in str(exc.value)

    def test_tilde_on_a_real_op_still_works(self):
        """The operator keeps its one real job — trigger control on a node
        that actually has a ready-count."""
        from operonx.core import PARENT, op
        from operonx.core.ops import SoftEdge

        @op
        def a(x: int) -> dict:
            return {"o": x}

        # Bind first: operonx names an op from its assignment target, and
        # pytest's assertion rewriting would otherwise supply its own
        # temporary (`@py_assert4`), which is not a legal op name.
        node = a(x=PARENT["x"])
        marker = ~node
        assert isinstance(marker, SoftEdge)
