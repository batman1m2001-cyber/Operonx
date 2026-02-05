"""Tests for auto-naming nodes from variable assignment."""

import pytest
from hush.core.nodes.base import BaseNode
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.transform.code_node import CodeNode
from hush.core.nodes.transform.parser_node import ParserNode
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.iteration.for_loop_node import ForLoopNode
from hush.core.nodes.flow.branch_node import BranchNode


class TestAutoName:
    """Test that nodes auto-infer name from variable assignment."""

    def test_base_node_auto_name(self):
        my_node = BaseNode()
        assert my_node.name == "my_node"

    def test_graph_node_auto_name(self):
        graph = GraphNode()
        assert graph.name == "graph"

    def test_code_node_auto_name(self):
        processor = CodeNode(code_fn=lambda: None)
        assert processor.name == "processor"

    def test_parser_node_auto_name(self):
        parser = ParserNode(format="json", extract=["x"])
        assert parser.name == "parser"

    def test_map_node_auto_name(self):
        """MapNode has depth 4 (MapNode → BaseIterationNode → GraphNode → BaseNode)."""
        mapper = MapNode()
        assert mapper.name == "mapper"

    def test_for_loop_node_auto_name(self):
        """ForLoopNode inherits BaseIterationNode.__init__ (depth 3)."""
        loop = ForLoopNode()
        assert loop.name == "loop"

    def test_branch_node_auto_name(self):
        router = BranchNode()
        assert router.name == "router"

    def test_explicit_name_preserved(self):
        node = BaseNode(name="explicit")
        assert node.name == "explicit"

    def test_no_assignment_falls_back(self):
        """When not assigned to a variable, falls back to unique_name()."""
        nodes = [BaseNode()]
        # Should not crash, should have some generated name
        assert nodes[0].name is not None
        assert len(nodes[0].name) > 0
