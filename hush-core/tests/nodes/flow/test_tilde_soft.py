"""Test using ~ (tilde) for soft edges: a >> ~b means soft edge to b

Uses the actual implementation from hush.core.nodes.base
"""

import pytest
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.transform.code_node import CodeNode
from hush.core.nodes.base import START, END


class TestTildeSoftEdge:
    """Test ~ syntax for soft edges with real nodes."""

    def test_basic_soft_edge(self):
        """Test: a >> ~b creates soft edge"""
        with GraphNode(name="test") as graph:
            a = CodeNode(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = CodeNode(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})

            START >> a >> ~b >> END

        graph.build()

        # Check edges
        a_to_b = next((e for e in graph._edges if e.from_node == "a" and e.to_node == "b"), None)
        assert a_to_b is not None, "Edge a->b should exist"
        assert a_to_b.soft == True, "Edge a->b should be soft"

    def test_basic_hard_edge(self):
        """Test: a >> b creates hard edge"""
        with GraphNode(name="test") as graph:
            a = CodeNode(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = CodeNode(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})

            START >> a >> b >> END

        graph.build()

        # Check edges
        a_to_b = next((e for e in graph._edges if e.from_node == "a" and e.to_node == "b"), None)
        assert a_to_b is not None, "Edge a->b should exist"
        assert a_to_b.soft == False, "Edge a->b should be hard"

    def test_mixed_chain(self):
        """Test: a >> ~b >> c >> ~d >> e creates mixed edges"""
        with GraphNode(name="test") as graph:
            a = CodeNode(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = CodeNode(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})
            c = CodeNode(name="c", code_fn=lambda y: {"z": y}, inputs={"y": b["y"]})
            d = CodeNode(name="d", code_fn=lambda z: {"w": z}, inputs={"z": c["z"]})
            e = CodeNode(name="e", code_fn=lambda w: {"v": w}, inputs={"w": d["w"]})

            START >> a >> ~b >> c >> ~d >> e >> END

        graph.build()

        # Check each edge
        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges}

        assert edges.get(("a", "b")) == True, "a->b should be soft"
        assert edges.get(("b", "c")) == False, "b->c should be hard"
        assert edges.get(("c", "d")) == True, "c->d should be soft"
        assert edges.get(("d", "e")) == False, "d->e should be hard"

    def test_branch_pattern(self):
        """Test branch pattern: branch >> ~case1 >> merge"""
        with GraphNode(name="test") as graph:
            branch = CodeNode(name="branch", code_fn=lambda: {"x": 1}, inputs={})
            case1 = CodeNode(name="case1", code_fn=lambda x: {"y": x}, inputs={"x": branch["x"]})
            case2 = CodeNode(name="case2", code_fn=lambda x: {"y": x}, inputs={"x": branch["x"]})
            merge = CodeNode(name="merge", code_fn=lambda y: {"z": y}, inputs={"y": case1["y"]})

            START >> branch
            branch >> ~case1 >> merge
            branch >> ~case2 >> merge
            merge >> END

        graph.build()

        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges}

        assert edges.get(("branch", "case1")) == True, "branch->case1 should be soft"
        assert edges.get(("branch", "case2")) == True, "branch->case2 should be soft"
        assert edges.get(("case1", "merge")) == False, "case1->merge should be hard"
        assert edges.get(("case2", "merge")) == False, "case2->merge should be hard"

    def test_start_soft_edge_raises_error(self):
        """Test that START >> ~node raises error"""
        with pytest.raises(TypeError, match="Không thể dùng soft edge"):
            with GraphNode(name="test") as graph:
                a = CodeNode(name="a", code_fn=lambda: {"x": 1}, inputs={})
                START >> ~a  # Should raise

    def test_all_soft_chain(self):
        """Test: a >> ~b >> ~c >> ~d creates all soft edges"""
        with GraphNode(name="test") as graph:
            a = CodeNode(name="a", code_fn=lambda: {"x": 1}, inputs={})
            b = CodeNode(name="b", code_fn=lambda x: {"y": x}, inputs={"x": a["x"]})
            c = CodeNode(name="c", code_fn=lambda y: {"z": y}, inputs={"y": b["y"]})
            d = CodeNode(name="d", code_fn=lambda z: {"w": z}, inputs={"z": c["z"]})

            START >> a >> ~b >> ~c >> ~d >> END

        graph.build()

        edges = {(e.from_node, e.to_node): e.soft for e in graph._edges}

        assert edges.get(("a", "b")) == True, "a->b should be soft"
        assert edges.get(("b", "c")) == True, "b->c should be soft"
        assert edges.get(("c", "d")) == True, "c->d should be soft"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
