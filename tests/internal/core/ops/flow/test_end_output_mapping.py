"""Tests for automatic output mapping with node >> END syntax.

When a node connects to END without explicit outputs, all auto-parsed output
keys are automatically forwarded to the parent graph.

Only applies when node doesn't have explicit outputs defined.
"""

import pytest

from operonx.core import END, PARENT, START, FuncOp, GraphOp, Operon
from operonx.core.states.ref import Ref


class TestAutoOutputMapping:
    """Test automatic output mapping with node >> END."""

    def test_wildcard_output_mapping(self):
        """node >> END sets all output keys to forward to PARENT."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="process", code_fn=lambda: {"a": 1, "b": 2})
            START >> node >> END

        assert node.outputs is not None
        # All auto-parsed output keys should have Ref to PARENT
        assert "a" in node.outputs
        assert "b" in node.outputs
        assert node.outputs["a"].value.var == "a"
        assert node.outputs["b"].value.var == "b"

    def test_explicit_outputs_not_overwritten(self):
        """node with explicit outputs is not overwritten by >> END."""
        with GraphOp(name="test") as graph:
            node = FuncOp(
                name="process",
                code_fn=lambda: {"result": 42},
                outputs={"custom": PARENT},
            )
            START >> node >> END

        # Should keep explicit outputs
        assert "custom" in node.outputs
        assert node.outputs["custom"].value is not None


class TestEndOutputMappingExecution:
    """Test that auto-mapped outputs work correctly during execution."""

    @pytest.mark.asyncio
    async def test_wildcard_execution(self):
        """Workflow with node >> END forwards all outputs."""
        with GraphOp(name="test") as graph:
            node = FuncOp(
                name="compute",
                code_fn=lambda: {"a": 1, "b": 2, "c": 3},
            )
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        assert result["a"] == 1
        assert result["b"] == 2
        assert result["c"] == 3

    @pytest.mark.asyncio
    async def test_pipeline_with_auto_outputs(self):
        """Multi-node pipeline with auto outputs works."""
        with GraphOp(name="pipeline") as graph:
            step1 = FuncOp(name="step1", code_fn=lambda: {"value": 10})
            step2 = FuncOp(
                name="step2",
                code_fn=lambda v: {"doubled": v * 2},
                inputs={"v": step1["value"]},
            )
            START >> step1 >> step2 >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        assert result["doubled"] == 20

    @pytest.mark.asyncio
    async def test_with_inputs_and_auto_outputs(self):
        """Workflow with inputs and auto outputs works."""
        with GraphOp(name="test") as graph:
            node = FuncOp(
                name="greet",
                code_fn=lambda name: {"message": f"Hello, {name}!"},
                inputs={"name": PARENT["name"]},
            )
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={"name": "World"})

        assert result["message"] == "Hello, World!"


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_list_of_nodes_to_end(self):
        """[node1, node2] >> END sets outputs on all nodes."""
        with GraphOp(name="test") as graph:
            node1 = FuncOp(name="n1", code_fn=lambda: {"x": 1})
            node2 = FuncOp(name="n2", code_fn=lambda: {"y": 2})
            START >> [node1, node2] >> END

        # Each node's output keys should forward to PARENT
        assert "x" in node1.outputs
        assert node1.outputs["x"].value.var == "x"
        assert "y" in node2.outputs
        assert node2.outputs["y"].value.var == "y"

    def test_parent_getitem_returns_ref(self):
        """PARENT["key"] returns Ref."""
        ref = PARENT["key"]
        assert isinstance(ref, Ref)

    def test_start_getitem_returns_ref(self):
        """START["key"] returns Ref."""
        ref = START["key"]
        assert isinstance(ref, Ref)

    def test_end_getitem_returns_ref(self):
        """END["key"] returns Ref (no special EndRef anymore)."""
        ref = END["key"]
        assert isinstance(ref, Ref)
