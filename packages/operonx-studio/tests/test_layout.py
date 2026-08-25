"""Layered layout.

These assert the properties a reader depends on — edges point forward, no
node is silently dropped, and the same IR always draws the same picture —
rather than exact pixel values, which would break on any spacing tweak.
"""

from __future__ import annotations

import pytest
from operonx_studio.layout import layout_graph

pytestmark = pytest.mark.unit


def ir(nodes, edges, entries=(), exits=()):
    return {
        "nodes": [{"id": f"g.{n}", "name": n, "kind": "FuncOp"} for n in nodes],
        "edges": [{"from": a, "to": b, **kw} for a, b, *rest in edges for kw in (rest[0] if rest else {},)],
        "entries": list(entries),
        "exits": list(exits),
    }


class TestLayering:
    def test_chain_advances_one_layer_per_hop(self):
        out = layout_graph(ir(["a", "b", "c"], [("a", "b"), ("b", "c")], entries=["a"]))
        layers = {n.name: n.layer for n in out.nodes}
        assert layers == {"a": 0, "b": 1, "c": 2}

    def test_fan_out_shares_a_layer(self):
        out = layout_graph(ir(["a", "b", "c"], [("a", "b"), ("a", "c")], entries=["a"]))
        layers = {n.name: n.layer for n in out.nodes}
        assert layers["b"] == layers["c"] == 1

    def test_diamond_uses_longest_path(self):
        """`d` must sit after `c`, not beside it — else the edge points backwards."""
        out = layout_graph(
            ir(["a", "b", "c", "d"],
               [("a", "b"), ("a", "c"), ("b", "c"), ("c", "d")], entries=["a"])
        )
        layers = {n.name: n.layer for n in out.nodes}
        assert layers["a"] < layers["b"] < layers["c"] < layers["d"]

    def test_every_edge_points_forward(self):
        out = layout_graph(
            ir(["a", "b", "c", "d"],
               [("a", "b"), ("b", "d"), ("a", "c"), ("c", "d")], entries=["a"])
        )
        depth = {n.id: n.layer for n in out.nodes}
        assert all(depth[e.src] < depth[e.dst] for e in out.edges)


class TestNothingIsDropped:
    def test_disconnected_node_is_still_placed(self):
        """A node the reader cannot see is how a diagram starts lying."""
        out = layout_graph(ir(["a", "b", "orphan"], [("a", "b")], entries=["a"]))
        assert {n.name for n in out.nodes} == {"a", "b", "orphan"}

    def test_edge_to_an_unknown_node_is_dropped_not_faked(self):
        """START/END are boundaries, not nodes — they must not become ghosts."""
        graph = ir(["a"], [("a", "b")], entries=["a"])
        out = layout_graph(graph)
        assert [n.name for n in out.nodes] == ["a"]
        assert out.edges == []

    def test_empty_graph(self):
        out = layout_graph(ir([], []))
        assert out.nodes == [] and out.edges == []


class TestEdgeSemantics:
    def test_edge_carries_origin_and_softness(self):
        out = layout_graph(
            ir(["a", "b"], [("a", "b", {"soft": True, "origin": "auto_soft", "type": "condition"})],
               entries=["a"])
        )
        edge = out.edges[0]
        assert edge.soft and edge.origin == "auto_soft" and edge.type == "condition"

    def test_non_advancing_edge_is_marked_as_a_return_path(self):
        """Drawn as a loop rather than a stray line across the canvas."""
        out = layout_graph(ir(["a", "b"], [("a", "b"), ("b", "a")], entries=["a"]))
        assert any(e.back for e in out.edges)


class TestDeterminism:
    def test_same_ir_gives_the_same_picture(self):
        graph = ir(["a", "b", "c", "d", "e"],
                   [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "e")], entries=["a"])
        first = layout_graph(graph)
        second = layout_graph(graph)
        assert [(n.id, n.x, n.y) for n in first.nodes] == [(n.id, n.x, n.y) for n in second.nodes]


class TestCanvas:
    def test_canvas_covers_every_node(self):
        out = layout_graph(
            ir(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("a", "d")], entries=["a"])
        )
        from operonx_studio.layout import NODE_H, NODE_W

        assert all(n.x + NODE_W <= out.width and n.y + NODE_H <= out.height for n in out.nodes)

    def test_nodes_in_a_layer_do_not_overlap(self):
        out = layout_graph(
            ir(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("a", "d")], entries=["a"])
        )
        ys = sorted(n.y for n in out.nodes if n.layer == 1)
        from operonx_studio.layout import NODE_H

        assert all(b - a >= NODE_H for a, b in zip(ys, ys[1:]))
