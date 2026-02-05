"""Shared fixtures and utilities for pytest test suite."""

import pytest
import asyncio
from typing import Dict, Any

from hush.core import (
    GraphNode,
    CodeNode,
    code_node,
    START, END, PARENT,
    StateSchema,
    MemoryState,
)


# ============================================================
# Common Test Utilities
# ============================================================

def assert_test(name: str, condition: bool):
    """Helper function for test assertions with descriptive names."""
    assert condition, f"Test failed: {name}"


# ============================================================
# Common Fixtures
# ============================================================

@pytest.fixture
def simple_code_fn():
    """Simple code function that doubles input."""
    return lambda x: {"result": x * 2}


@pytest.fixture
def add_fn():
    """Code function that adds two numbers."""
    return lambda a, b: {"result": a + b}


@pytest.fixture
def increment_fn():
    """Code function that increments by 1."""
    return lambda x: {"x": x + 1}


# ============================================================
# Graph Fixtures
# ============================================================

@pytest.fixture
def simple_graph():
    """Create a simple single-node graph."""
    @code_node
    def double(x: int):
        return {"result": x * 2}

    with GraphNode(name="simple_graph") as graph:
        node = double(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
        START >> node >> END

    graph.build()
    return graph


@pytest.fixture
def linear_graph():
    """Create a linear two-node graph: add_10 -> multiply_2."""
    with GraphNode(name="linear_graph") as graph:
        node_a = CodeNode(
            name="add_10",
            code_fn=lambda x: {"result": x + 10},
            inputs={"x": PARENT["x"]}
        )
        node_b = CodeNode(
            name="multiply_2",
            code_fn=lambda x: {"result": x * 2},
            inputs={"x": node_a["result"]},
            outputs={"*": PARENT}
        )
        START >> node_a >> node_b >> END

    graph.build()
    return graph


# ============================================================
# State Fixtures
# ============================================================

@pytest.fixture
def create_state():
    """Factory fixture to create state from a graph with inputs."""
    def _create_state(graph: GraphNode, inputs: Dict[str, Any] = None) -> MemoryState:
        schema = StateSchema(graph)
        return schema.create_state(inputs=inputs or {})
    return _create_state
